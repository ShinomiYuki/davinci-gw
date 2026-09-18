"""标准配置表三张执行输入页的冻结表头契约。"""

REFERENCE_SHEET = "引用数据"
DIRECT_SHEET = "直接报文路由"
SIGNAL_SHEET = "信号路由"
DIAGNOSTIC_SHEET = "诊断报文路由"

DIAGNOSTIC_TRANSPORT_FIELDS = ("N_As", "N_Bs", "N_Cs", "N_Ar", "N_Br", "N_Cr", "BlockSize", "STmin")
DIAGNOSTIC_HEADERS = (
    "诊断请求端报文名称", "诊断请求端CANID_REQ", "诊断请求端接收报文类型",
    "诊断请求端CANID_RES", "诊断请求端发送报文类型", "诊断请求端Length", "诊断请求端CAN通道",
    "诊断应答端报文名称", "诊断应答端CANID_REQ", "诊断应答端发送报文类型",
    "诊断应答端CANID_RES", "诊断应答端接收报文类型", "诊断应答端Length", "诊断应答端CAN通道",
) + tuple(f"诊断{role}端{field}" for role in ("请求", "应答") for field in DIAGNOSTIC_TRANSPORT_FIELDS) + (
    "诊断入口类型", "操作类型",
)

REFERENCE_HEADERS = ("CAN通道名称", "CanIfTxBuffer名称", "CanIfHrh名称")
DIRECT_HEADERS = (
    "源网段报文名称", "源网段报文CANID", "源网段报文Length", "源网段报文类型",
    "源网段CAN通道", "源网段RxIndicationUL", "源网段报文Checksum使能",
    "源网段报文Dlc Check使能", "目标网段报文名称", "目标网段报文CANID",
    "目标网段报文Length", "目标网段报文类型", "目标网段CAN通道",
    "目标网段报文Checksum使能", "目标网段报文PnFilter使能",
    "目标网段报文Truncation使能", "路由Length Strategy功能选择", "操作类型",
)
SIGNAL_HEADERS = (
    "源网段", "源报文名", "源信号名", "字节序", "超时值", "超时时间",
    "源信号组合名", "目标网段", "目标报文名", "目标信号名", "操作类型",
)

DIRECT_IDENTITY_FIELDS = (
    "源网段报文名称", "源网段报文CANID", "源网段CAN通道",
    "目标网段报文名称", "目标网段报文CANID", "目标网段CAN通道",
)
DIRECT_ADD_REQUIRED_FIELDS = DIRECT_IDENTITY_FIELDS + (
    "源网段报文Length", "源网段报文类型", "源网段RxIndicationUL",
    "源网段报文Dlc Check使能", "目标网段报文Length", "目标网段报文类型",
    "目标网段报文Truncation使能", "路由Length Strategy功能选择",
)
SIGNAL_IDENTITY_FIELDS = (
    "源网段", "源报文名", "源信号名", "目标网段", "目标报文名", "目标信号名",
)
