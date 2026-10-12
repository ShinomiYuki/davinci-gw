"""按相同目标 PDU 查找必须共用队列的传输协议路由。"""

from davinci_gw.arxml.index import autosar_path
from davinci_gw.domain.models import MutationAction
from davinci_gw.modules import definitions as d
from davinci_gw.modules.common import definition_ref, semantic_values


class TransportQueueGroups:
    """同一路径及引用同一目标 PDU 的路径属于同一队列组。"""

    def __init__(self, index, namespace):
        self.destinations = {}
        for node in index.find_by_definition_ref(d.PDUR_PATH):
            parameters, _ = semantic_values(node, namespace)
            if parameters.get(d.PDUR_PATH_COMM_TYPE) != ("TRANSPORT_PROTOCOL",):
                continue
            path = autosar_path(node, namespace)
            self.destinations[path] = []
            for child in node.iter():
                if definition_ref(child, namespace) == d.PDUR_DEST:
                    _, references = semantic_values(child, namespace)
                    self.destinations[path].append((
                        references.get(d.PDUR_DEST_PDU_REF, ()),
                        references.get(d.PDUR_QUEUE_REF, ()),
                    ))

    def connected_queues(self, path, target_pdu=None, operations=()):
        """包含尚未应用的新增目标，并追踪跨路径的间接关联。"""
        destinations = {key: list(value) for key, value in self.destinations.items()}
        for operation in operations:
            if (operation.action is MutationAction.CREATE
                    and operation.definition_ref == d.PDUR_PATH
                    and operation.parameter(d.PDUR_PATH_COMM_TYPE) == "TRANSPORT_PROTOCOL"):
                destinations[operation.object_path] = []
        for operation in operations:
            if (operation.action is MutationAction.CREATE
                    and operation.definition_ref == d.PDUR_DEST
                    and operation.parent_path in destinations):
                destinations[operation.parent_path].append((
                    (operation.reference(d.PDUR_DEST_PDU_REF),),
                    (operation.reference(d.PDUR_QUEUE_REF),),
                ))
        paths_by_pdu = {}
        for parent, entries in destinations.items():
            for pdus, _ in entries:
                for pdu in pdus:
                    paths_by_pdu.setdefault(pdu, set()).add(parent)
        pending = {path}
        if target_pdu:
            pending.update(paths_by_pdu.get(target_pdu, ()))
        result = {}
        while pending:
            parent = pending.pop()
            if parent in result:
                continue
            entries = destinations.get(parent, ())
            result[parent] = tuple(queues for _, queues in entries)
            for pdus, _ in entries:
                for pdu in pdus:
                    pending.update(paths_by_pdu.get(pdu, set()) - result.keys())
        return result
