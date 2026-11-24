"""OTA 白名单的只读规划和经确认的新文件生成。"""

from __future__ import annotations

import hashlib
import io
import os
import re
import tempfile
import time
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
from threading import RLock
from uuid import uuid4

from lxml import etree as E

from davinci_gw.arxml.document import ArxmlDocument, XML_DECLARATION_PATTERN, STANDALONE_PATTERN
from davinci_gw.arxml.index import autosar_path
from davinci_gw.runtime import CancellationToken

NETWORKS = ('BD', 'CH', 'DA', 'DK', 'DM', 'EP', 'GL', 'IC', 'LC', 'PT', 'SU')
ARBITRATION = '/MICROSAR/BswM/BswMConfig/BswMArbitration'
CONTROL = '/MICROSAR/BswM/BswMConfig/BswMModeControl'
GROUP = '/MICROSAR/Com/ComConfig/ComIPduGroup'
STATES = {
    'TX': ('DCM_ENABLE_RX_DISABLE_TX_NORM', 'DCM_DISABLE_RX_TX_NORMAL',
           'DCM_ENABLE_RX_DISABLE_TX_NORM_NM', 'DCM_DISABLE_RX_TX_NORM_NM'),
    'RX': ('DCM_DISABLE_RX_ENABLE_TX_NORM', 'DCM_DISABLE_RX_TX_NORMAL',
           'DCM_DISABLE_RX_ENABLE_TX_NORM_NM', 'DCM_DISABLE_RX_TX_NORM_NM'),
}
MANUAL = ['工具保留所有 ComIPduGroupRef；请手动移除白名单报文的普通组引用并关联对应 OTA 组。',
          '关闭条件变为 False 不会自动启动组；恢复依赖保留的标准启用规则。请在 DaVinci 和实际网络验证恢复边界。']


class OtaConflict(ValueError):
    """无法安全确定配置目标。"""


class OtaPlanner:
    """在内存投影上建立控制链，不改变输入文件。"""

    def __init__(self, document: ArxmlDocument):
        self.doc = document
        self.ns = document.namespace
        self.changes: list[dict] = []
        self.coverage: list[dict] = []
        self.issues: list[dict] = []
        self.nodes = list(document.root.iter(self.q('ECUC-CONTAINER-VALUE')))
        self.paths = {}
        self.definitions = {}
        for node in self.nodes:
            path = self.path(node)
            if path in self.paths:
                raise OtaConflict(f'重复对象路径：{path}')
            self.paths[path] = node
            self.definitions.setdefault(self.definition(node), []).append(node)

    def q(self, tag):
        return f'{{{self.ns}}}{tag}'

    def text(self, node, tag):
        return node.findtext(self.q(tag)) or ''

    def definition(self, node):
        return self.text(node, 'DEFINITION-REF')

    def path(self, node):
        return autosar_path(node, self.ns)

    def values(self, node, field, recursive=True):
        found = []
        for owner in node.iter() if recursive else (node,):
            for group in owner:
                if group.tag not in (self.q('PARAMETER-VALUES'), self.q('REFERENCE-VALUES')):
                    continue
                for entry in group:
                    if self.definition(entry).rsplit('/', 1)[-1] == field:
                        found.append(self.text(entry, 'VALUE') or self.text(entry, 'VALUE-REF'))
        return found

    def one(self, values, context):
        if len(values) != 1:
            raise OtaConflict(f'{context}：需要唯一对象或值，实际 {len(values)} 个。')
        return values[0]

    def value(self, node, field):
        return self.one(self.values(node, field), self.path(node) + '/' + field)

    def resolve(self, path):
        if path not in self.paths:
            raise OtaConflict(f'关键内部引用不存在：{path}')
        return self.paths[path]

    def typed(self, definition, parent=None):
        return [n for n in self.definitions.get(definition, ()) if
                (parent is None or n.getparent().getparent() is parent)]

    def child(self, parent, tag):
        node = parent.find(self.q(tag))
        if node is None:
            node = E.SubElement(parent, self.q(tag))
        return node

    def container(self, name, definition, choice=False):
        node = E.Element(self.q('ECUC-CONTAINER-VALUE'), UUID=str(uuid4()))
        E.SubElement(node, self.q('SHORT-NAME')).text = name
        E.SubElement(node, self.q('DEFINITION-REF'), DEST=(
            'ECUC-CHOICE-CONTAINER-DEF' if choice else 'ECUC-PARAM-CONF-CONTAINER-DEF')).text = definition
        return node

    def parameter(self, node, field, value, kind='ENUMERATION'):
        numerical = kind in ('BOOLEAN', 'INTEGER')
        entry = E.SubElement(self.child(node, 'PARAMETER-VALUES'), self.q(
            'ECUC-NUMERICAL-PARAM-VALUE' if numerical else 'ECUC-TEXTUAL-PARAM-VALUE'))
        E.SubElement(entry, self.q('DEFINITION-REF'), DEST=f'ECUC-{kind}-PARAM-DEF').text = self.definition(node) + '/' + field
        E.SubElement(entry, self.q('VALUE')).text = str(value)

    def reference(self, node, field, value, kind='REFERENCE', dest='ECUC-CONTAINER-VALUE'):
        entry = E.SubElement(self.child(node, 'REFERENCE-VALUES'), self.q('ECUC-REFERENCE-VALUE'))
        E.SubElement(entry, self.q('DEFINITION-REF'), DEST=f'ECUC-{kind}-DEF').text = self.definition(node) + '/' + field
        E.SubElement(entry, self.q('VALUE-REF'), DEST=dest).text = value

    def attach(self, parent, node):
        expected_path = self.path(parent) + '/' + self.text(node, 'SHORT-NAME')
        if expected_path in self.paths:
            raise OtaConflict('对象名称与已有对象冲突：' + expected_path)
        self.child(parent, 'SUB-CONTAINERS').append(node)
        for new in node.iter(self.q('ECUC-CONTAINER-VALUE')):
            self.nodes.append(new)
            self.paths[self.path(new)] = new
            self.definitions.setdefault(self.definition(new), []).append(new)
        self.changes.append({'operation': 'ADD', 'path': self.path(node), 'before': None,
                             'after': self.describe(node)})
        return node

    def describe(self, node):
        return {'definition': self.definition(node), 'values': self.signature(node)}

    def signature(self, node):
        result = []
        for entry in node.iter():
            if entry.tag in (self.q('ECUC-REFERENCE-VALUE'), self.q('ECUC-TEXTUAL-PARAM-VALUE'), self.q('ECUC-NUMERICAL-PARAM-VALUE')):
                result.append((self.definition(entry), self.text(entry, 'VALUE') or self.text(entry, 'VALUE-REF')))
        return sorted(result)

    def ensure(self, parent, node, *, reuse=True, ignore=()):
        def expanded(n, seen=frozenset()):
            result = []
            for field, value in self.signature(n):
                if field.rsplit('/', 1)[-1] in ignore:
                    continue
                if field.endswith(('/BswMArgumentRef', '/BswMRuleExpressionRef')):
                    if value in seen:
                        raise OtaConflict('表达式循环引用：' + value)
                    target = self.resolve(value)
                    result.append((field, repr(expanded(target, seen | {value}))))
                else:
                    result.append((field, value))
            return sorted(result)
        signature = expanded
        expected = signature(node)
        named = [n for n in self.typed(self.definition(node), parent) if self.text(n, 'SHORT-NAME') == self.text(node, 'SHORT-NAME')]
        if named:
            existing = self.one(named, self.text(node, 'SHORT-NAME'))
            if signature(existing) != expected:
                self.issues.append({'category': 'PARAMETER_OR_REFERENCE_DIFFERENCE', 'path': self.path(existing),
                                    'before': self.describe(existing), 'expected': self.describe(node)})
                raise OtaConflict(f'已有同名对象参数或引用冲突：{self.path(existing)}')
            return existing
        if reuse:
            equivalents = [n for n in self.typed(self.definition(node), parent) if signature(n) == expected]
            if equivalents:
                return equivalents[0]
        return self.attach(parent, node)

    def allocate(self, definition, field, parent):
        used = []
        for node in self.typed(definition, parent):
            value = self.value(node, field)
            if not value.isdecimal():
                raise OtaConflict(f'无效编号：{self.path(node)}={value}')
            used.append(int(value))
        if len(used) != len(set(used)):
            raise OtaConflict(f'{field} 编号重复，必须先解决基线冲突。')
        return next(i for i in range(len(used) + 1) if i not in used)

    def condition(self, arb, name, port, comparison, value, mode=False):
        node = self.container(name, ARBITRATION + '/BswMModeCondition')
        self.parameter(node, 'BswMConditionType', comparison)
        self.reference(node, 'BswMConditionMode', self.path(port))
        choice = self.container('BswMConditionValue', self.definition(node) + '/BswMConditionValue', True)
        self.child(node, 'SUB-CONTAINERS').append(choice)
        leaf_name = 'BswMModeDeclaration' if mode else 'BswMBswMode'
        leaf = self.container(leaf_name, self.definition(choice) + '/' + leaf_name)
        self.child(choice, 'SUB-CONTAINERS').append(leaf)
        if mode:
            self.reference(leaf, 'BswMModeValueRef', value, 'FOREIGN-REFERENCE', 'MODE-DECLARATION')
        else:
            self.parameter(leaf, 'BswMBswRequestedMode', value, 'STRING')
        return self.ensure(arb, node)

    def expression(self, arb, name, operator, arguments):
        node = self.container(name, ARBITRATION + '/BswMLogicalExpression')
        self.parameter(node, 'BswMLogicalOperator', operator)
        for arg in arguments:
            self.reference(node, 'BswMArgumentRef', self.path(arg), 'CHOICE-REFERENCE')
        return self.ensure(arb, node)

    def set_reference(self, node, field, path):
        old = self.value(node, field)
        if old == path:
            return
        for entry in node.iter(self.q('ECUC-REFERENCE-VALUE')):
            if self.definition(entry).endswith('/' + field):
                entry.find(self.q('VALUE-REF')).text = path
        self.changes.append({'operation': 'MODIFY_REFERENCE', 'path': self.path(node) + '/' + field,
                             'before': old, 'after': path})

    def action(self, control, group, enable):
        name = ('CC_EnablePDUGroup_' if enable else 'CC_DisablePDUGroup_') + self.text(group, 'SHORT-NAME')
        node = self.container(name, CONTROL + '/BswMAction')
        choice = self.container('BswMAvailableActions', self.definition(node) + '/BswMAvailableActions', True)
        self.child(node, 'SUB-CONTAINERS').append(choice)
        switch = self.container('BswMPduGroupSwitch', self.definition(choice) + '/BswMPduGroupSwitch')
        self.child(choice, 'SUB-CONTAINERS').append(switch)
        self.parameter(switch, 'BswMPduGroupSwitchReinit', 'false', 'BOOLEAN')
        self.reference(switch, 'BswMEnabledPduGroupRef' if enable else 'BswMDisabledPduGroupRef', self.path(group), 'SYMBOLIC-NAME-REFERENCE')
        return self.ensure(control, node)

    def item(self, action_list, action):
        existing = [n for n in self.typed(CONTROL + '/BswMActionList/BswMActionListItem', action_list)
                    if self.values(n, 'BswMActionListItemRef') == [self.path(action)]]
        if existing:
            return
        node = self.container(self.text(action, 'SHORT-NAME'), CONTROL + '/BswMActionList/BswMActionListItem')
        self.parameter(node, 'BswMAbortOnFail', 'false', 'BOOLEAN')
        self.parameter(node, 'BswMActionListItemIndex', self.allocate(self.definition(node), 'BswMActionListItemIndex', action_list), 'INTEGER')
        self.reference(node, 'BswMActionListItemRef', self.path(action), 'CHOICE-REFERENCE')
        self.attach(action_list, node)

    def list_actions(self, action_list):
        return [self.resolve(self.value(n, 'BswMActionListItemRef')) for n in
                self.typed(CONTROL + '/BswMActionList/BswMActionListItem', action_list)]

    def monitoring(self, control, ordinary, ota):
        """沿已有超时监控动作及列表建立白名单组的等效关联。"""
        for action in list(self.typed(CONTROL + '/BswMAction', control)):
            fields = ('BswMDisabledDMPduGroupRef', 'BswMEnabledDMPduGroupRef')
            if not any(self.path(ordinary) in self.values(action, f) for f in fields):
                continue
            clone = deepcopy(action)
            operation = 'DisableDM' if self.path(ordinary) in self.values(action, fields[0]) else 'EnableDM'
            clone.find(self.q('SHORT-NAME')).text = 'CC_' + operation + '_' + self.text(ota, 'SHORT-NAME')
            for entry in clone.iter():
                if 'UUID' in entry.attrib:
                    entry.set('UUID', str(uuid4()))
                if entry.tag == self.q('VALUE-REF') and entry.text == self.path(ordinary):
                    entry.text = self.path(ota)
            for entry in list(clone.iter(self.q('ECUC-REFERENCE-VALUE'))):
                if self.definition(entry).rsplit('/', 1)[-1] in fields:
                    if self.text(entry, 'VALUE-REF') != self.path(ota):
                        entry.getparent().remove(entry)
            admin = clone.find(self.q('ADMIN-DATA'))
            if admin is not None:
                clone.remove(admin)
            new = self.ensure(control, clone)
            for action_list in list(self.typed(CONTROL + '/BswMActionList', control)):
                if action in self.list_actions(action_list):
                    self.item(action_list, new)

    def process_network(self, network):
        ports = self.typed(ARBITRATION + '/BswMModeRequestPort')
        # 网段标识来自真实通道引用，后续所有关联都沿引用解析。
        def belongs(path):
            name = path.rsplit('/', 1)[-1]
            if 'Messagelis' in name:
                return bool(re.search(r'(?:^|_)' + network + r'Messagelis(?:_|$)', name))
            return bool(re.search(r'(?:^|_)' + network + r'(?:CAN)?(?:_|$)', name))
        can = self.one([p for p in ports if any(belongs(v) for v in self.values(p, 'BswMCanSMChannelRef'))], network + ' CanSM 入口')
        channel = self.value(can, 'BswMCanSMChannelRef')
        arb = can.getparent().getparent()
        control = self.one(self.typed(CONTROL, arb.getparent().getparent()), network + ' BswMModeControl')
        dcm = self.one([p for p in ports if self.values(p, 'BswMDcmComMChannelRef') == [channel] and p.getparent().getparent() is arb], network + ' DCM 入口')
        mode = self.one([p for p in ports if self.values(p, 'BswMSwcModeNotificationModeDeclarationGroupPrototypeRef') == ['/PortInterfaces/swc_send_OTA/Mode'] and p.getparent().getparent() is arb], network + ' OTA 模式入口')
        nonota = self.condition(arb, 'OTA_NotActive', mode, 'BSWM_EQUALS_NOT', '/ModeDclrGroups/OTA_Sts/OTA', True)
        rules = self.typed(ARBITRATION + '/BswMRule', arb)
        nm_rules = []
        for rule in rules:
            true = self.values(rule, 'BswMRuleTrueActionList')
            false = self.values(rule, 'BswMRuleFalseActionList')
            if len(true) != 1 or len(false) != 1:
                continue
            disable, enable = self.resolve(true[0]), self.resolve(false[0])
            da, ea = self.list_actions(disable), self.list_actions(enable)
            if any(self.values(a, 'BswMComMNetworkHandleRef') == [channel] and self.values(a, 'BswMNMAction') == ['BSWM_NM_DISABLE'] for a in da):
                if not any(self.values(a, 'BswMComMNetworkHandleRef') == [channel] and self.values(a, 'BswMNMAction') == ['BSWM_NM_ENABLE'] for a in ea):
                    raise OtaConflict(network + ' NM 启用动作通道错误')
                nm_rules.append(rule)
        nm_rule = self.one(nm_rules, network + ' NM Rule')
        original = self.resolve(self.value(nm_rule, 'BswMRuleExpressionRef'))
        if self.values(original, 'BswMLogicalOperator') == ['BSWM_AND']:
            args = [self.resolve(p) for p in self.values(original, 'BswMArgumentRef')]
            if any(self.signature(a) == self.signature(nonota) for a in args):
                original = self.one([a for a in args if self.signature(a) != self.signature(nonota)], network + ' 原 NM 表达式')
        if self.definition(original) != ARBITRATION + '/BswMLogicalExpression':
            raise OtaConflict(network + ' 原 NM 禁用表达式类型错误')
        nm = self.expression(arb, network + '_NM_TX', 'BSWM_AND', [nonota, original])
        self.set_reference(nm_rule, 'BswMRuleExpressionRef', self.path(nm))
        for direction in ('RX', 'TX'):
            before = len(self.changes)
            # 标准启停列表通过已有 Rule 关联，并由通道表达式的引用闭包核实。
            def depends(node, port, seen=None):
                seen = set() if seen is None else seen
                path = self.path(node)
                if path in seen:
                    return False
                seen.add(path)
                refs = self.values(node, 'BswMArgumentRef') + self.values(node, 'BswMConditionMode')
                return self.path(port) in refs or any(depends(self.resolve(p), port, seen) for p in refs)
            standard = []
            for rule in rules:
                if not self.text(rule, 'SHORT-NAME').endswith('_' + direction):
                    continue
                expr = self.resolve(self.value(rule, 'BswMRuleExpressionRef'))
                if depends(expr, can) and depends(expr, dcm):
                    standard.append(rule)
            standard_rule = self.one(standard, network + ' ' + direction + ' 标准 Rule')
            enable_list = self.resolve(self.value(standard_rule, 'BswMRuleTrueActionList'))
            disable_list = self.resolve(self.value(standard_rule, 'BswMRuleFalseActionList'))
            group_paths = [p for a in self.list_actions(enable_list) for p in self.values(a, 'BswMEnabledPduGroupRef')]
            ordinary = self.one([self.resolve(p) for p in group_paths if not p.endswith('_OTA')], network + ' ' + direction + ' 普通 Com Group')
            disabled = [p for a in self.list_actions(disable_list)
                        for p in self.values(a, 'BswMDisabledPduGroupRef') if not p.endswith('_OTA')]
            if disabled != [self.path(ordinary)]:
                raise OtaConflict(network + ' ' + direction + ' 标准启用和禁用列表的普通组引用不一致')
            group_parent = ordinary.getparent().getparent()
            ota_groups = [self.resolve(p) for p in group_paths if p.endswith('_OTA')]
            ordinary_name = self.text(ordinary, 'SHORT-NAME')
            direction_name = re.match(r'^(.*_(?:Rx|Tx))(?:_.*)?$', ordinary_name, re.IGNORECASE)
            if direction_name:
                if not direction_name[1].lower().endswith('_' + direction.lower()):
                    raise OtaConflict(network + ' ' + direction + ' 普通组名称包含相反的收发方向')
                name = direction_name[1] + '_OTA'
            else:
                name = ordinary_name + '_' + direction.title() + '_OTA'
            group_node = self.container(name, GROUP)
            self.parameter(group_node, 'ComIPduGroupHandleId', self.allocate(GROUP, 'ComIPduGroupHandleId', group_parent), 'INTEGER')
            if ota_groups:
                group = self.one(ota_groups, network + ' ' + direction + ' OTA Group')
            else:
                group = self.ensure(group_parent, group_node, reuse=False, ignore=('ComIPduGroupHandleId',))
            disable_action = self.action(control, group, False)
            enable_action = self.action(control, group, True)
            self.item(enable_list, enable_action)
            if direction == 'RX':
                self.monitoring(control, ordinary, group)
            for item in list(self.typed(CONTROL + '/BswMActionList/BswMActionListItem', disable_list)):
                actual_action = self.resolve(self.value(item, 'BswMActionListItemRef'))
                targets = self.values(actual_action, 'BswMDisabledPduGroupRef')
                if self.path(group) in targets:
                    if (targets != [self.path(group)] or
                        self.signature(actual_action) != self.signature(disable_action)):
                        raise OtaConflict('关闭动作包含其他控制或参数差异，不能安全移除列表项：' + self.path(actual_action))
                    self.changes.append({'operation': 'REMOVE_REFERENCE_ITEM', 'path': self.path(item),
                                         'before': self.describe(item), 'after': None})
                    item_path = self.path(item)
                    item.getparent().remove(item)
                    self.nodes.remove(item)
                    self.definitions[self.definition(item)].remove(item)
                    del self.paths[item_path]
            al_name = 'BswMActionList_' + network + 'CAN_' + direction + '_disable_OTA'
            action_list_node = self.container(al_name, CONTROL + '/BswMActionList')
            self.parameter(action_list_node, 'BswMActionListExecution', 'BSWM_TRIGGER')
            # 先构造完整列表，按实际动作引用复用异名专用列表。
            item_node = self.container(self.text(disable_action, 'SHORT-NAME'), CONTROL + '/BswMActionList/BswMActionListItem')
            self.parameter(item_node, 'BswMAbortOnFail', 'false', 'BOOLEAN')
            self.parameter(item_node, 'BswMActionListItemIndex', '0', 'INTEGER')
            self.reference(item_node, 'BswMActionListItemRef', self.path(disable_action), 'CHOICE-REFERENCE')
            self.child(action_list_node, 'SUB-CONTAINERS').append(item_node)
            action_list = self.ensure(control, action_list_node)
            conditions = [self.condition(arb, f'{network}_{direction}_OTA_Dcm_{i}', dcm, 'BSWM_EQUALS', state) for i, state in enumerate(STATES[direction])]
            low = self.expression(arb, network + 'CAN_' + direction + '_disable_OTA_Condition', 'BSWM_OR', conditions)
            middle = self.expression(arb, network + '_' + direction + '_OTA', 'BSWM_AND', [nonota, low])
            network_condition = self.condition(arb, network + '_' + direction + '_OTA_Network', can,
                                               'BSWM_EQUALS_NOT' if direction == 'TX' else 'BSWM_EQUALS',
                                               'CANSM_BSWM_FULL_COMMUNICATION' if direction == 'TX' else 'CANSM_BSWM_NO_COMMUNICATION')
            outer = self.expression(arb, network + '_' + direction + '_Control', 'BSWM_OR', [network_condition, middle])
            rule = self.container('BswMRule_' + network + 'CAN_' + direction + '_disable_OTA', ARBITRATION + '/BswMRule')
            self.parameter(rule, 'BswMNestedExecutionOnly', 'false', 'BOOLEAN')
            self.parameter(rule, 'BswMRuleId', self.allocate(self.definition(rule), 'BswMRuleId', arb), 'INTEGER')
            self.parameter(rule, 'BswMRuleInitState', 'BSWM_FALSE')
            self.reference(rule, 'BswMRuleExpressionRef', self.path(outer))
            self.reference(rule, 'BswMRuleTrueActionList', self.path(action_list))
            partial = [r for r in self.typed(self.definition(rule), arb)
                       if self.values(r, 'BswMRuleTrueActionList') == [self.path(action_list)]
                       and self.values(r, 'BswMRuleExpressionRef') == [self.path(middle)]]
            if partial:
                existing = self.one(partial, network + ' ' + direction + ' 未完成的 OTA Rule')
                if (self.values(existing, 'BswMNestedExecutionOnly') != ['false'] or
                    self.values(existing, 'BswMRuleInitState') != ['BSWM_FALSE'] or
                    self.values(existing, 'BswMRuleFalseActionList')):
                    raise OtaConflict('已有 OTA Rule 参数冲突：' + self.path(existing))
                self.set_reference(existing, 'BswMRuleExpressionRef', self.path(outer))
            self.ensure(arb, rule, ignore=('BswMRuleId',))
            self.coverage.append({'network': network, 'direction': direction, 'group': self.path(group),
                                  'status': 'COMPLETE' if len(self.changes) == before else 'MISSING_CONFIGURATION',
                                  'manual_membership_required': True})

    def plan(self):
        for network in NETWORKS:
            try:
                self.process_network(network)
            except OtaConflict as exc:
                self.issues.append({'category': 'CONFLICT_OR_MISSING_REFERENCE', 'network': network, 'message': str(exc)})
        groups = [entry['group'] for entry in self.coverage]
        if len(groups) != len(set(groups)):
            self.issues.append({'category': 'CONFLICT', 'message': '多个网段或方向共用同一个 OTA 组，不能安全生成。'})
        return {'can_generate': not self.issues, 'changes': self.changes, 'issues': self.issues,
                'coverage': self.coverage, 'manual_steps': list(MANUAL)}


class OtaService:
    """保存有时限的一次性预览；写出时核实输入与用户确认。"""

    def __init__(self):
        self._sessions = OrderedDict()
        self._lock = RLock()

    def check(self, baseline: str) -> dict:
        return self._preview(baseline, prepare=False)

    def preview(self, baseline: str) -> dict:
        return self._preview(baseline, prepare=True)

    def _preview(self, baseline, prepare):
        with self._lock:
            path = Path(baseline)
            if str(path).replace('/', '\\').startswith('\\\\'):
                raise ValueError('禁止网络或设备路径，请选择本地 ARXML。')
            if not path.is_absolute() or path.suffix.lower() != '.arxml':
                raise ValueError('请输入本地绝对 ARXML 路径。')
            content = path.read_bytes()
            declaration = XML_DECLARATION_PATTERN.match(content[:512])
            standalone = STANDALONE_PATTERN.search(declaration.group(1)) if declaration else None
            tree = E.parse(io.BytesIO(content), E.XMLParser(resolve_entities=False, no_network=True,
                                                          remove_blank_text=False, huge_tree=True))
            doc = ArxmlDocument(path.resolve(), tree, has_xml_declaration=declaration is not None,
                                standalone=None if standalone is None else standalone.group(1).lower() == b'yes')
            planner = OtaPlanner(doc)
            report = planner.plan()
            report['preparation_id'] = None
            if prepare and report['can_generate']:
                identifier = str(uuid4())
                self._sessions[identifier] = (time.monotonic(), path.resolve(), hashlib.sha256(content).digest(), doc, deepcopy(report))
                while len(self._sessions) > 4:
                    self._sessions.popitem(last=False)
                report['preparation_id'] = identifier
            return report

    def generate(self, preparation_id: str, output: str, confirmed: bool = False,
                 *, cancellation: CancellationToken | None = None) -> dict:
        with self._lock:
            token = cancellation or CancellationToken()
            token.checkpoint('ota_generate')
            if confirmed is not True:
                raise ValueError('必须先展示预览并取得用户明确确认。')
            if preparation_id not in self._sessions:
                raise ValueError('预览不存在、已使用或已失效，请重新预览。')
            created, baseline, fingerprint, doc, report = self._sessions[preparation_id]
            if time.monotonic() - created > 1800:
                del self._sessions[preparation_id]
                raise ValueError('预览已过期，请重新预览。')
            if hashlib.sha256(baseline.read_bytes()).digest() != fingerprint:
                del self._sessions[preparation_id]
                raise ValueError('基线在预览后已变化，请重新预览。')
            target = Path(output)
            if str(target).replace('/', '\\').startswith('\\\\'):
                raise ValueError('禁止网络或设备路径，请选择本地输出。')
            if not target.is_absolute() or target.suffix.lower() != '.arxml':
                raise ValueError('输出必须是新的本地绝对 ARXML 路径。')
            target = target.resolve()
            if target == baseline or target.exists():
                raise ValueError('禁止覆盖基线或已有输出。')
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = None
            try:
                with tempfile.NamedTemporaryFile(dir=target.parent, suffix='.tmp.arxml', delete=False) as stream:
                    temp = Path(stream.name)
                doc.tree.write(str(temp), encoding=doc.encoding, xml_declaration=doc.has_xml_declaration,
                               pretty_print=False, **({'standalone': doc.standalone} if doc.standalone is not None else {}))
                verified = OtaPlanner(ArxmlDocument.load(temp)).plan()
                if not verified['can_generate'] or verified['changes']:
                    raise OtaConflict('输出控制链复核失败，未写入最终文件。')
                if hashlib.sha256(baseline.read_bytes()).digest() != fingerprint:
                    raise ValueError('写出前基线已变化，请重新预览。')
                token.checkpoint('ota_commit')
                # Windows rename 不覆盖已有目标，避免检查与提交间覆盖其他输出。
                if os.name == 'nt':
                    os.rename(temp, target)
                else:
                    os.link(temp, target)
                    temp.unlink()
                temp = None
                del self._sessions[preparation_id]
                return {**report, 'output_path': str(target), 'generated': True}
            finally:
                if temp is not None:
                    temp.unlink(missing_ok=True)
