"""人工最小 Com/BswM 工程的 OTA 控制链和文件安全验证。"""

from copy import deepcopy

import pytest
from lxml import etree as E

from davinci_gw.application.ota import OtaPlanner, OtaService
from davinci_gw.runtime.cancellation import CancellationToken, OperationCancelled

NS = 'http://autosar.org/schema/r4.0'
NETWORKS = ('BD', 'CH', 'DA', 'DK', 'DM', 'EP', 'GL', 'IC', 'LC', 'PT', 'SU')
ARB = '/MICROSAR/BswM/BswMConfig/BswMArbitration'
CTL = '/MICROSAR/BswM/BswMConfig/BswMModeControl'
COM = '/MICROSAR/Com/ComConfig'


def tag(name):
    return f'{{{NS}}}{name}'


def text(node, name):
    return node.findtext(tag(name)) or ''


def path(node):
    names = [text(n, 'SHORT-NAME') for n in reversed(list(node.iterancestors()))]
    return '/' + '/'.join(n for n in names + [text(node, 'SHORT-NAME')] if n)


def group(node, name):
    existing = node.find(tag(name))
    return existing if existing is not None else E.SubElement(node, tag(name))


def container(parent, name, definition):
    node = E.SubElement(group(parent, 'SUB-CONTAINERS'), tag('ECUC-CONTAINER-VALUE'))
    E.SubElement(node, tag('SHORT-NAME')).text = name
    E.SubElement(node, tag('DEFINITION-REF'), DEST='ECUC-PARAM-CONF-CONTAINER-DEF').text = definition
    return node


def param(node, field, value, kind='ENUMERATION'):
    numerical = kind in ('BOOLEAN', 'INTEGER')
    entry = E.SubElement(group(node, 'PARAMETER-VALUES'), tag(
        'ECUC-NUMERICAL-PARAM-VALUE' if numerical else 'ECUC-TEXTUAL-PARAM-VALUE'))
    E.SubElement(entry, tag('DEFINITION-REF'), DEST=f'ECUC-{kind}-PARAM-DEF').text = text(node, 'DEFINITION-REF') + '/' + field
    E.SubElement(entry, tag('VALUE')).text = str(value)


def ref(node, field, value):
    entry = E.SubElement(group(node, 'REFERENCE-VALUES'), tag('ECUC-REFERENCE-VALUE'))
    E.SubElement(entry, tag('DEFINITION-REF'), DEST='ECUC-REFERENCE-DEF').text = text(node, 'DEFINITION-REF') + '/' + field
    E.SubElement(entry, tag('VALUE-REF'), DEST='ECUC-CONTAINER-VALUE').text = value


def values(node, field):
    entries = node.iter()
    return [text(e, 'VALUE') or text(e, 'VALUE-REF') for e in entries
            if text(e, 'DEFINITION-REF').endswith('/' + field) and
            e.tag != tag('ECUC-CONTAINER-VALUE')]


def named(root, name):
    return next(n for n in root.iter(tag('ECUC-CONTAINER-VALUE')) if text(n, 'SHORT-NAME') == name)


def by_path(root, target):
    return next(n for n in root.iter(tag('ECUC-CONTAINER-VALUE')) if path(n) == target)


def condition(arb, name, port, state):
    node = container(arb, name, ARB + '/BswMModeCondition')
    param(node, 'BswMConditionType', 'BSWM_EQUALS')
    ref(node, 'BswMConditionMode', path(port))
    value = container(node, 'BswMConditionValue', ARB + '/BswMModeCondition/BswMConditionValue')
    mode = container(value, 'BswMBswMode', text(value, 'DEFINITION-REF') + '/BswMBswMode')
    param(mode, 'BswMBswRequestedMode', state, 'STRING')
    return node


def expression(arb, name, arguments):
    node = container(arb, name, ARB + '/BswMLogicalExpression')
    param(node, 'BswMLogicalOperator', 'BSWM_OR')
    for arg in arguments:
        ref(node, 'BswMArgumentRef', path(arg))
    return node


def action_list(control, name, actions):
    node = container(control, name, CTL + '/BswMActionList')
    param(node, 'BswMActionListExecution', 'BSWM_TRIGGER')
    for i, action in enumerate(actions):
        item = container(node, text(action, 'SHORT-NAME'), CTL + '/BswMActionList/BswMActionListItem')
        param(item, 'BswMActionListItemIndex', i, 'INTEGER')
        param(item, 'BswMAbortOnFail', 'false', 'BOOLEAN')
        ref(item, 'BswMActionListItemRef', path(action))
    return node


def rule(arb, name, number, expr, true, false):
    node = container(arb, name, ARB + '/BswMRule')
    param(node, 'BswMRuleId', number, 'INTEGER')
    param(node, 'BswMRuleInitState', 'BSWM_FALSE')
    param(node, 'BswMNestedExecutionOnly', 'false', 'BOOLEAN')
    ref(node, 'BswMRuleExpressionRef', path(expr))
    ref(node, 'BswMRuleTrueActionList', path(true))
    ref(node, 'BswMRuleFalseActionList', path(false))
    return node


def switch(control, name, com_group, enable):
    action = container(control, name, CTL + '/BswMAction')
    choice = container(action, 'BswMAvailableActions', CTL + '/BswMAction/BswMAvailableActions')
    leaf = container(choice, 'BswMPduGroupSwitch', text(choice, 'DEFINITION-REF') + '/BswMPduGroupSwitch')
    param(leaf, 'BswMPduGroupSwitchReinit', 'false', 'BOOLEAN')
    ref(leaf, 'BswMEnabledPduGroupRef' if enable else 'BswMDisabledPduGroupRef', path(com_group))
    return action


def port(arb, name, source_name, field, target):
    node = container(arb, name, ARB + '/BswMModeRequestPort')
    choice = container(node, 'BswMModeRequestSource', text(node, 'DEFINITION-REF') + '/BswMModeRequestSource')
    source = container(choice, source_name, text(choice, 'DEFINITION-REF') + '/' + source_name)
    ref(source, field, target)
    return node


@pytest.fixture
def baseline(tmp_path):
    root = E.Element(tag('AUTOSAR'), nsmap={None: NS})
    package = E.SubElement(E.SubElement(root, tag('AR-PACKAGES')), tag('AR-PACKAGE'))
    E.SubElement(package, tag('SHORT-NAME')).text = 'ActiveEcuC'
    elements = E.SubElement(package, tag('ELEMENTS'))
    modules = {}
    for name in ('Com', 'BswM'):
        module = E.SubElement(elements, tag('ECUC-MODULE-CONFIGURATION-VALUES'))
        E.SubElement(module, tag('SHORT-NAME')).text = name
        E.SubElement(module, tag('DEFINITION-REF')).text = '/MICROSAR/' + name
        modules[name] = module
    config = container(modules['Com'], 'ComConfig', COM)
    partition = container(modules['BswM'], 'Core0', '/MICROSAR/BswM/BswMConfig')
    arb = container(partition, 'BswMArbitration', ARB)
    control = container(partition, 'BswMModeControl', CTL)
    port(arb, 'ExistingMode', 'BswMSwcModeNotification',
         'BswMSwcModeNotificationModeDeclarationGroupPrototypeRef', '/PortInterfaces/swc_send_OTA/Mode')
    for index, network in enumerate(NETWORKS):
        channel = '/ActiveEcuC/ComM/ComMConfigSet/CAN_' + network
        can = port(arb, network + '_Can', 'BswMCanSMIndication', 'BswMCanSMChannelRef', channel)
        dcm = port(arb, network + '_Dcm', 'BswMDcmComModeRequest', 'BswMDcmComMChannelRef', channel)
        for direction in ('RX', 'TX'):
            com_group = container(config, f'{network}_{direction}_normal', COM + '/ComIPduGroup')
            param(com_group, 'ComIPduGroupHandleId', index * 2 + (direction == 'TX'), 'INTEGER')
            pdu = container(config, network + '_' + direction + '_Message', COM + '/ComIPdu')
            ref(pdu, 'ComIPduGroupRef', path(com_group))
            enable = switch(control, f'{network}_{direction}_Enable', com_group, True)
            disable = switch(control, f'{network}_{direction}_Disable', com_group, False)
            enabled = action_list(control, f'{network}_{direction}_Enabled', [enable])
            disabled = action_list(control, f'{network}_{direction}_Disabled', [disable])
            network_cond = condition(arb, f'{network}_{direction}_NetCond', can, 'CANSM_BSWM_FULL_COMMUNICATION')
            dcm_cond = condition(arb, f'{network}_{direction}_DcmCond', dcm, 'DCM_ENABLE_RX_TX_NORM_NM')
            expr = expression(arb, f'{network}_{direction}_Standard', [network_cond, dcm_cond])
            rule(arb, network + '_' + direction, index * 3 + (direction == 'TX'), expr, enabled, disabled)
        nm_actions = []
        for state in ('DISABLE', 'ENABLE'):
            action = container(control, network + '_NM_' + state, CTL + '/BswMAction')
            choice = container(action, 'BswMAvailableActions', text(action, 'DEFINITION-REF') + '/BswMAvailableActions')
            nm = container(choice, 'BswMNMControl', text(choice, 'DEFINITION-REF') + '/BswMNMControl')
            ref(nm, 'BswMComMNetworkHandleRef', channel)
            param(nm, 'BswMNMAction', 'BSWM_NM_' + state)
            nm_actions.append(action_list(control, network + '_Nm' + state, [action]))
        nm_condition = condition(arb, network + '_NmCondition', dcm, 'DCM_DISABLE_RX_TX_NM')
        nm_expr = expression(arb, network + '_OriginalNM', [nm_condition])
        rule(arb, network + '_NM', index * 3 + 2, nm_expr, *nm_actions)
    file = tmp_path / 'baseline.arxml'
    E.ElementTree(root).write(str(file), encoding='UTF-8', xml_declaration=True)
    return file


def generate(baseline, tmp_path):
    service = OtaService()
    preview = service.preview(str(baseline))
    assert preview['can_generate'], preview['issues']
    output = tmp_path / 'configured.arxml'
    service.generate(preview['preparation_id'], str(output), confirmed=True)
    return service, preview, output, E.parse(str(output)).getroot()


def evaluate(root, node, inputs):
    operator = values(node, 'BswMLogicalOperator')
    if operator:
        results = [evaluate(root, by_path(root, p), inputs) for p in values(node, 'BswMArgumentRef')]
        return all(results) if operator == ['BSWM_AND'] else any(results)
    expected = (values(node, 'BswMBswRequestedMode') or values(node, 'BswMModeValueRef'))[0]
    actual = inputs[values(node, 'BswMConditionMode')[0]]
    return actual != expected if values(node, 'BswMConditionType') == ['BSWM_EQUALS_NOT'] else actual == expected


def test_complete_control_chains_and_membership_preservation(baseline, tmp_path):
    original = E.parse(str(baseline)).getroot()
    original_bytes = baseline.read_bytes()
    _, preview, output, root = generate(baseline, tmp_path)
    assert baseline.read_bytes() == original_bytes
    assert len(preview['coverage']) == 22
    assert {(c['network'], c['direction']) for c in preview['coverage']} == {
        (n, d) for n in NETWORKS for d in ('RX', 'TX')}
    assert values(root, 'ComIPduGroupRef') == values(original, 'ComIPduGroupRef')
    groups = [n for n in root.iter(tag('ECUC-CONTAINER-VALUE'))
              if text(n, 'DEFINITION-REF') == COM + '/ComIPduGroup']
    assert len([n for n in groups if text(n, 'SHORT-NAME').endswith('_OTA')]) == 22
    ids = [values(n, 'ComIPduGroupHandleId')[0] for n in groups]
    assert len(ids) == len(set(ids))
    for network in NETWORKS:
        nm_rule = named(root, network + '_NM')
        nm_expr = by_path(root, values(nm_rule, 'BswMRuleExpressionRef')[0])
        assert values(nm_expr, 'BswMLogicalOperator') == ['BSWM_AND']
        assert path(named(root, network + '_OriginalNM')) in values(nm_expr, 'BswMArgumentRef')
        old_nm = named(original, network + '_NM')
        assert E.tostring(named(root, network + '_OriginalNM')) == E.tostring(named(original, network + '_OriginalNM'))
        for field in ('BswMRuleTrueActionList', 'BswMRuleFalseActionList'):
            assert values(nm_rule, field) == values(old_nm, field)
        for direction in ('RX', 'TX'):
            rule_node = named(root, f'BswMRule_{network}CAN_{direction}_disable_OTA')
            assert values(rule_node, 'BswMRuleInitState') == ['BSWM_FALSE']
            assert values(rule_node, 'BswMNestedExecutionOnly') == ['false']
            assert not values(rule_node, 'BswMRuleFalseActionList')
            outer = by_path(root, values(rule_node, 'BswMRuleExpressionRef')[0])
            assert values(outer, 'BswMLogicalOperator') == ['BSWM_OR']
            outer_args = [by_path(root, p) for p in values(outer, 'BswMArgumentRef')]
            network_cond = next(a for a in outer_args if values(a, 'BswMConditionMode'))
            assert values(network_cond, 'BswMConditionMode') == [path(named(root, network + '_Can'))]
            assert values(network_cond, 'BswMConditionType') == ['BSWM_EQUALS_NOT' if direction == 'TX' else 'BSWM_EQUALS']
            assert values(network_cond, 'BswMBswRequestedMode') == [
                'CANSM_BSWM_FULL_COMMUNICATION' if direction == 'TX' else 'CANSM_BSWM_NO_COMMUNICATION']
            middle = next(a for a in outer_args if values(a, 'BswMLogicalOperator'))
            assert values(middle, 'BswMLogicalOperator') == ['BSWM_AND']
            middle_args = [by_path(root, p) for p in values(middle, 'BswMArgumentRef')]
            mode_cond = next(a for a in middle_args if values(a, 'BswMModeValueRef'))
            assert values(mode_cond, 'BswMConditionType') == ['BSWM_EQUALS_NOT']
            assert values(mode_cond, 'BswMModeValueRef') == ['/ModeDclrGroups/OTA_Sts/OTA']
            low = next(a for a in middle_args if values(a, 'BswMLogicalOperator'))
            assert values(low, 'BswMLogicalOperator') == ['BSWM_OR']
            conditions = [by_path(root, p) for p in values(low, 'BswMArgumentRef')]
            expected = {'DCM_DISABLE_RX_TX_NORMAL', 'DCM_DISABLE_RX_TX_NORM_NM'}
            expected.update({'DCM_ENABLE_RX_DISABLE_TX_NORM', 'DCM_ENABLE_RX_DISABLE_TX_NORM_NM'} if direction == 'TX'
                            else {'DCM_DISABLE_RX_ENABLE_TX_NORM', 'DCM_DISABLE_RX_ENABLE_TX_NORM_NM'})
            assert len(conditions) == 4
            assert {values(c, 'BswMBswRequestedMode')[0] for c in conditions} == expected
            assert all(values(c, 'BswMConditionType') == ['BSWM_EQUALS'] and
                       values(c, 'BswMConditionMode') == [path(named(root, network + '_Dcm'))] for c in conditions)
            action_list_node = by_path(root, values(rule_node, 'BswMRuleTrueActionList')[0])
            assert values(action_list_node, 'BswMActionListExecution') == ['BSWM_TRIGGER']
            action = by_path(root, values(action_list_node, 'BswMActionListItemRef')[0])
            target_group = by_path(root, values(action, 'BswMDisabledPduGroupRef')[0])
            assert text(target_group, 'SHORT-NAME') == f'{network}_{direction}_OTA'
            inputs = {path(named(root, network + '_Can')): 'CANSM_BSWM_FULL_COMMUNICATION',
                      path(named(root, network + '_Dcm')): 'DCM_DISABLE_RX_TX_NORMAL',
                      path(named(root, 'ExistingMode')): '/ModeDclrGroups/OTA_Sts/nomal'}
            assert evaluate(root, outer, inputs)
            inputs[path(named(root, 'ExistingMode'))] = '/ModeDclrGroups/OTA_Sts/OTA'
            assert not evaluate(root, outer, inputs)
            inputs[path(named(root, network + '_Can'))] = 'CANSM_BSWM_SILENT_COMMUNICATION'
            assert evaluate(root, outer, inputs) is (direction == 'TX')
            inputs[path(named(root, network + '_Can'))] = 'CANSM_BSWM_NO_COMMUNICATION'
            assert evaluate(root, outer, inputs)
    check = OtaService().preview(str(output))
    assert check['can_generate'], check['issues']
    assert check['changes'] == []


def test_confirmation_change_detection_and_single_use(baseline, tmp_path):
    service = OtaService()
    preview = service.preview(str(baseline))
    output = tmp_path / 'new.arxml'
    with pytest.raises(ValueError, match='确认'):
        service.generate(preview['preparation_id'], str(output))
    assert not output.exists()
    baseline.write_bytes(baseline.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='变化'):
        service.generate(preview['preparation_id'], str(output), confirmed=True)
    preview = service.preview(str(baseline))
    service.generate(preview['preparation_id'], str(output), confirmed=True)
    with pytest.raises(ValueError, match='不存在|已使用|失效'):
        service.generate(preview['preparation_id'], str(tmp_path / 'second.arxml'), confirmed=True)


def test_check_is_read_only_and_does_not_prepare(baseline):
    content = baseline.read_bytes()
    report = OtaService().check(str(baseline))
    assert report['can_generate'], report['issues']
    assert report['preparation_id'] is None
    assert baseline.read_bytes() == content


@pytest.mark.parametrize('target_name', ['baseline.arxml', 'existing.arxml'])
def test_never_overwrites(baseline, tmp_path, target_name):
    target = tmp_path / target_name
    if target != baseline:
        target.write_text('keep', encoding='utf-8')
    original = target.read_bytes()
    service = OtaService()
    preview = service.preview(str(baseline))
    with pytest.raises(ValueError, match='覆盖'):
        service.generate(preview['preparation_id'], str(target), confirmed=True)
    assert target.read_bytes() == original


@pytest.mark.parametrize('problem', ['missing_port', 'ambiguous_port', 'group_id', 'rule_id', 'broken_reference'])
def test_blocks_conflicts(baseline, problem):
    tree = E.parse(str(baseline))
    root = tree.getroot()
    if problem == 'missing_port':
        node = named(root, 'BD_Dcm')
        node.getparent().remove(node)
    elif problem == 'ambiguous_port':
        node = deepcopy(named(root, 'BD_Can'))
        node.find(tag('SHORT-NAME')).text = 'SecondCan'
        named(root, 'BD_Can').getparent().append(node)
    else:
        field = {'group_id': 'ComIPduGroupHandleId', 'rule_id': 'BswMRuleId',
                 'broken_reference': 'BswMRuleExpressionRef'}[problem]
        entries = [e for e in root.iter() if text(e, 'DEFINITION-REF').endswith('/' + field)
                   and e.tag != tag('ECUC-CONTAINER-VALUE')]
        if problem == 'broken_reference':
            entries[0].find(tag('VALUE-REF')).text = '/ActiveEcuC/BswM/Missing'
        else:
            entries[1].find(tag('VALUE')).text = text(entries[0], 'VALUE')
    tree.write(str(baseline), encoding='UTF-8')
    report = OtaService().preview(str(baseline))
    assert not report['can_generate']
    assert report['preparation_id'] is None
    assert report['issues']


def test_removes_only_standard_ota_disable_item(baseline, tmp_path):
    _, _, output, root = generate(baseline, tmp_path)
    control = named(root, 'BswMModeControl')
    standard = named(root, 'BD_RX_Disabled')
    ota_action = next(n for n in root.iter(tag('ECUC-CONTAINER-VALUE'))
                      if text(n, 'DEFINITION-REF') == CTL + '/BswMAction' and
                      values(n, 'BswMDisabledPduGroupRef') == [path(named(root, 'BD_RX_OTA'))])
    extra = action_list(control, 'TemporaryAutoConfiguration', [ota_action])
    item = extra.find(tag('SUB-CONTAINERS'))[0]
    item.find(tag('PARAMETER-VALUES'))[0].find(tag('VALUE')).text = '1'
    group(standard, 'SUB-CONTAINERS').append(item)
    extra.getparent().remove(extra)
    dm = container(control, 'KeepDeadlineAction', CTL + '/BswMAction')
    ref(dm, 'BswMDisabledDMPduGroupRef', path(named(root, 'BD_RX_OTA')))
    action_list(control, 'KeepDeadlineList', [dm])
    E.ElementTree(root).write(str(output), encoding='UTF-8')
    service = OtaService()
    preview = service.preview(str(output))
    assert preview['can_generate'], preview['issues']
    removed = [c for c in preview['changes'] if c['operation'] == 'REMOVE_REFERENCE_ITEM']
    assert len(removed) == 1
    assert '/BD_RX_Disabled/' in removed[0]['path']
    target = tmp_path / 'adjusted.arxml'
    service.generate(preview['preparation_id'], str(target), confirmed=True)
    adjusted = E.parse(str(target)).getroot()
    assert named(adjusted, 'KeepDeadlineAction') is not None
    assert values(named(adjusted, 'KeepDeadlineList'), 'BswMActionListItemRef') == [path(dm)]
    assert values(named(adjusted, 'BD_RX_Disabled'), 'BswMActionListItemRef') == [path(named(adjusted, 'BD_RX_Disable'))]
    assert by_path(adjusted, path(ota_action)) is not None
    enable_refs = values(named(adjusted, 'BD_RX_Enabled'), 'BswMActionListItemRef')
    assert any(values(by_path(adjusted, p), 'BswMEnabledPduGroupRef') == [path(named(adjusted, 'BD_RX_OTA'))]
               for p in enable_refs)


def test_partial_configuration_and_wrong_ota_reference(baseline, tmp_path):
    _, _, output, root = generate(baseline, tmp_path)
    target_rule = named(root, 'BswMRule_BDCAN_RX_disable_OTA')
    target_rule.getparent().remove(target_rule)
    E.ElementTree(root).write(str(output), encoding='UTF-8')
    preview = OtaService().preview(str(output))
    assert preview['can_generate'], preview['issues']
    assert len(preview['changes']) == 1
    assert preview['changes'][0]['path'].endswith('/BswMRule_BDCAN_RX_disable_OTA')
    condition_node = named(root, 'BD_RX_OTA_Network')
    entry = next(e for e in condition_node.iter() if text(e, 'DEFINITION-REF').endswith('/BswMConditionMode'))
    entry.find(tag('VALUE-REF')).text = path(named(root, 'CH_Can'))
    E.ElementTree(root).write(str(output), encoding='UTF-8')
    report = OtaService().preview(str(output))
    assert not report['can_generate']
    assert any(i['category'] == 'PARAMETER_OR_REFERENCE_DIFFERENCE' for i in report['issues'])


@pytest.mark.parametrize('combined', [False, True], ids=['individual_groups', 'combined_bd_ch'])
def test_receiver_monitoring_preserves_original_actions_and_is_idempotent(baseline, tmp_path, combined):
    tree = E.parse(str(baseline))
    root = tree.getroot()
    control = named(root, 'BswMModeControl')
    originals = {}
    action_lists = []
    for state in ('Enabled', 'Disabled'):
        field = 'BswM' + state + 'DMPduGroupRef'
        for network in NETWORKS:
            if combined and network == 'CH':
                continue
            action = container(control, network + '_' + state + '_Monitoring', CTL + '/BswMAction')
            choice = container(action, 'BswMAvailableActions', text(action, 'DEFINITION-REF') + '/BswMAvailableActions')
            leaf = container(choice, 'BswMDeadlineMonitoringControl', text(choice, 'DEFINITION-REF') + '/BswMDeadlineMonitoringControl')
            ref(leaf, field, path(named(root, network + '_RX_normal')))
            if combined and network == 'BD':
                ref(leaf, field, path(named(root, 'CH_RX_normal')))
            originals[path(action)] = E.tostring(action)
            listing = action_list(control, network + '_' + state + '_MonitoringList', [action])
            action_lists.append((path(listing), path(action), field,
                                 ('BD', 'CH') if combined and network == 'BD' else (network,)))
    tree.write(str(baseline), encoding='UTF-8')
    _, _, output, adjusted = generate(baseline, tmp_path)
    for action_path, original in originals.items():
        assert E.tostring(by_path(adjusted, action_path)) == original
    for list_path, original_action, field, networks in action_lists:
        refs = values(by_path(adjusted, list_path), 'BswMActionListItemRef')
        assert original_action in refs
        added_actions = [by_path(adjusted, p) for p in refs if p != original_action]
        assert len(added_actions) == len(networks)
        assert {tuple(values(a, field)) for a in added_actions} == {
            (path(named(adjusted, n + '_RX_OTA')),) for n in networks}
        assert all(not values(a, 'BswMEnabledDMPduGroupRef' if field == 'BswMDisabledDMPduGroupRef'
                              else 'BswMDisabledDMPduGroupRef') for a in added_actions)
    preview = OtaService().preview(str(output))
    assert preview['can_generate'], preview['issues']
    assert preview['changes'] == []


@pytest.mark.parametrize('wrong_group', ['CH_RX_normal', 'BD_TX_normal'])
def test_standard_enable_group_cannot_cross_network_or_direction(baseline, wrong_group):
    tree = E.parse(str(baseline))
    root = tree.getroot()
    action = named(root, 'BD_RX_Enable')
    reference = next(e for e in action.iter() if text(e, 'DEFINITION-REF').endswith('/BswMEnabledPduGroupRef'))
    reference.find(tag('VALUE-REF')).text = path(named(root, wrong_group))
    tree.write(str(baseline), encoding='UTF-8')
    report = OtaService().preview(str(baseline))
    assert not report['can_generate'], '标准启用与禁用动作的 Com Group 不一致时必须阻断，不能把错组纳入本通道控制。'
    assert report['preparation_id'] is None


def test_group_names_without_hash_keep_receive_and_transmit_separate(baseline, tmp_path):
    tree = E.parse(str(baseline))
    root = tree.getroot()
    replacements = {}
    for network in NETWORKS:
        for direction in ('RX', 'TX'):
            node = named(root, f'{network}_{direction}_normal')
            old_path = path(node)
            node.find(tag('SHORT-NAME')).text = network + '_' + direction.title()
            replacements[old_path] = path(node)
    for entry in root.iter(tag('VALUE-REF')):
        if entry.text in replacements:
            entry.text = replacements[entry.text]
    tree.write(str(baseline), encoding='UTF-8')
    _, preview, _, adjusted = generate(baseline, tmp_path)
    group_paths = [c['group'] for c in preview['coverage']]
    assert len(group_paths) == len(set(group_paths)) == 22
    for network in NETWORKS:
        for direction in ('RX', 'TX'):
            rule_node = named(adjusted, f'BswMRule_{network}CAN_{direction}_disable_OTA')
            listing = by_path(adjusted, values(rule_node, 'BswMRuleTrueActionList')[0])
            action = by_path(adjusted, values(listing, 'BswMActionListItemRef')[0])
            ota_path = values(action, 'BswMDisabledPduGroupRef')[0]
            assert ota_path.endswith(f'/{network}_{direction.title()}_OTA')


@pytest.mark.parametrize('also_closes_other_group', [False, True], ids=['alias_only', 'mixed_groups_blocked'])
def test_standard_disable_alias_is_removed_by_actual_group_reference(baseline, tmp_path, also_closes_other_group):
    _, _, configured, root = generate(baseline, tmp_path)
    control = named(root, 'BswMModeControl')
    rule_node = named(root, 'BswMRule_BDCAN_RX_disable_OTA')
    ota_list = by_path(root, values(rule_node, 'BswMRuleTrueActionList')[0])
    ota_action = by_path(root, values(ota_list, 'BswMActionListItemRef')[0])
    alias = deepcopy(ota_action)
    alias.find(tag('SHORT-NAME')).text = 'IndependentlyNamedDisableAction'
    if also_closes_other_group:
        switch_node = next(n for n in alias.iter(tag('ECUC-CONTAINER-VALUE'))
                           if text(n, 'DEFINITION-REF').endswith('/BswMPduGroupSwitch'))
        ref(switch_node, 'BswMDisabledPduGroupRef', path(named(root, 'CH_RX_OTA')))
    group(control, 'SUB-CONTAINERS').append(alias)
    original_alias = E.tostring(alias)
    temporary_list = action_list(control, 'TemporaryAliasList', [alias])
    item = temporary_list.find(tag('SUB-CONTAINERS'))[0]
    item.find(tag('PARAMETER-VALUES'))[0].find(tag('VALUE')).text = '1'
    standard = named(root, 'BD_RX_Disabled')
    group(standard, 'SUB-CONTAINERS').append(item)
    temporary_list.getparent().remove(temporary_list)
    E.ElementTree(root).write(str(configured), encoding='UTF-8')
    service = OtaService()
    preview = service.preview(str(configured))
    if also_closes_other_group:
        assert not preview['can_generate']
        assert preview['preparation_id'] is None
        assert any('不能安全移除列表项' in issue.get('message', '') for issue in preview['issues'])
        return
    assert preview['can_generate'], preview['issues']
    removals = [c for c in preview['changes'] if c['operation'] == 'REMOVE_REFERENCE_ITEM']
    assert len(removals) == 1
    assert removals[0]['path'].endswith('/BD_RX_Disabled/IndependentlyNamedDisableAction')
    target = tmp_path / 'alias-adjusted.arxml'
    service.generate(preview['preparation_id'], str(target), confirmed=True)
    adjusted = E.parse(str(target)).getroot()
    assert E.tostring(by_path(adjusted, path(alias))) == original_alias
    assert values(named(adjusted, 'BD_RX_Disabled'), 'BswMActionListItemRef') == [path(named(adjusted, 'BD_RX_Disable'))]


def test_cancellation_after_output_verification_leaves_no_file(baseline, tmp_path, monkeypatch):
    service = OtaService()
    preview = service.preview(str(baseline))
    assert preview['can_generate'], preview['issues']
    original_content = baseline.read_bytes()
    target = tmp_path / 'cancelled.arxml'
    token = CancellationToken()
    original_plan = OtaPlanner.plan

    def cancel_after_verification(planner):
        report = original_plan(planner)
        token.cancel()
        return report

    monkeypatch.setattr(OtaPlanner, 'plan', cancel_after_verification)
    with pytest.raises(OperationCancelled):
        service.generate(preview['preparation_id'], str(target), confirmed=True, cancellation=token)
    assert token.is_cancelled
    assert not target.exists()
    assert not list(tmp_path.glob('*.tmp.arxml'))
    assert baseline.read_bytes() == original_content


def test_standard_disable_with_unrelated_enable_effect_is_blocked(baseline, tmp_path):
    _, _, configured, root = generate(baseline, tmp_path)
    control = named(root, 'BswMModeControl')
    rule_node = named(root, 'BswMRule_BDCAN_RX_disable_OTA')
    ota_list = by_path(root, values(rule_node, 'BswMRuleTrueActionList')[0])
    pure_disable = by_path(root, values(ota_list, 'BswMActionListItemRef')[0])
    mixed = deepcopy(pure_disable)
    mixed.find(tag('SHORT-NAME')).text = 'MixedDisableAndEnableAction'
    switch_node = next(n for n in mixed.iter(tag('ECUC-CONTAINER-VALUE'))
                       if text(n, 'DEFINITION-REF').endswith('/BswMPduGroupSwitch'))
    ref(switch_node, 'BswMEnabledPduGroupRef', path(named(root, 'CH_TX_normal')))
    group(control, 'SUB-CONTAINERS').append(mixed)
    standard = named(root, 'BD_RX_Disabled')
    temporary = action_list(control, 'TemporaryMixedList', [mixed])
    item = temporary.find(tag('SUB-CONTAINERS'))[0]
    item.find(tag('PARAMETER-VALUES'))[0].find(tag('VALUE')).text = '1'
    group(standard, 'SUB-CONTAINERS').append(item)
    temporary.getparent().remove(temporary)
    E.ElementTree(root).write(str(configured), encoding='UTF-8')
    original_content = configured.read_bytes()
    report = OtaService().preview(str(configured))
    assert not report['can_generate']
    assert report['preparation_id'] is None
    assert not any(c['operation'] == 'REMOVE_REFERENCE_ITEM' and
                   c['path'].endswith('/BD_RX_Disabled/MixedDisableAndEnableAction') for c in report['changes'])
    assert configured.read_bytes() == original_content
    preserved = E.parse(str(configured)).getroot()
    assert values(by_path(preserved, path(mixed)), 'BswMEnabledPduGroupRef') == [path(named(root, 'CH_TX_normal'))]
    assert path(mixed) in values(named(preserved, 'BD_RX_Disabled'), 'BswMActionListItemRef')
