# Manifest schema

Create one temporary UTF-8 JSON manifest per run. DoIP-to-CAN mapping-only runs use empty `routes`, `signal_routes` and `diagnostic_routes`; add `doip_can_mappings` as described below.

```json
{
  "output_version": "4.84",
  "source_workbook": "D:\\path\\Communication Routing Table.xlsx",
  "signal_matrix": "D:\\path\\Customer Signal Matrix.xlsx",
  "selected_versions": ["V4.83", "V4.84"],
  "project_mode": "AX",
  "routes": [
    {
      "operation": "ADD",
      "source_message_name": "Example_1",
      "source_can_id": "0x123",
      "source_length": 8,
      "source_channel": "ICCAN",
      "target_message_name": "Example_1",
      "target_can_id": "0x123",
      "target_length": 8,
      "target_channel": "GLCAN",
      "history_rows": [654, 670],
      "flzcu_row": 900
    }
  ],
  "signal_routes": [
    {
      "operation": "DELETE",
      "source_network": "CH",
      "source_message_name": "ACU_3",
      "source_signal_name": "ACU_3_CrashOutputSts",
      "byte_order": "Motorola LSB",
      "timeout_value": "0xFF",
      "timeout_value_source_sheet": "FLZCU_VCU_CH",
      "timeout_value_source_row": 123,
      "timeout_time": 0.5,
      "source_signal_combined_name": "",
      "target_network": "BD",
      "target_message_name": "ABM_1",
      "target_signal_name": "CrashOutputSts",
      "source_send_type": "Cyclic",
      "source_cycle_time_ms": 10,
      "target_column": "AH",
      "history_rows": [668],
      "flzcu_row": 5
    }
  ],
  "diagnostic_routes": [
    {
      "entry_type": "OBD_CAN",
      "operation": "ADD",
      "request_message_name": "OBD_Request",
      "request_can_id_req": "0x7DF",
      "request_rx_type": "STANDARD_NO_FD_CAN",
      "request_can_id_res": "0x7E8",
      "request_tx_type": "STANDARD_CAN",
      "request_length": 8,
      "request_channel": "DGCAN",
      "response_message_name": "ECU_Response",
      "response_can_id_req": "0x7DF",
      "response_tx_type": "STANDARD_FD_CAN",
      "response_can_id_res": "0x7E8",
      "response_rx_type": "STANDARD_FD_CAN",
      "response_length": 64,
      "response_channel": "ICCAN",
      "request_n_as": 70,
      "request_n_bs": 150,
      "request_n_cs": 25,
      "request_n_ar": 70,
      "request_n_br": 10,
      "request_n_cr": 150,
      "request_block_size": 0,
      "request_st_min": 0,
      "response_n_as": 70,
      "response_n_bs": 150,
      "response_n_cs": 25,
      "response_n_ar": 70,
      "response_n_br": 10,
      "response_n_cr": 150,
      "response_block_size": 0,
      "response_st_min": 0,
      "source_sheet": "DIAG Message routing(OBD CAN)",
      "source_row": 4
    },
    {
      "entry_type": "OBD_ETH",
      "operation": "DELETE",
      "request_message_name": "Diag_Request",
      "request_can_id_req": "0x700",
      "request_rx_type": "STANDARD_NO_FD_CAN",
      "request_can_id_res": "0x708",
      "request_tx_type": "STANDARD_CAN",
      "request_length": 8,
      "request_channel": "DGCAN",
      "response_message_name": "ECU_Response",
      "response_can_id_req": "0x700",
      "response_tx_type": "STANDARD_CAN",
      "response_can_id_res": "0x708",
      "response_rx_type": "STANDARD_NO_FD_CAN",
      "response_length": 8,
      "response_channel": "BDCAN",
      "request_n_as": 70,
      "request_n_bs": 150,
      "request_n_cs": 25,
      "request_n_ar": 70,
      "request_n_br": 10,
      "request_n_cr": 150,
      "request_block_size": 0,
      "request_st_min": 0,
      "response_n_as": 70,
      "response_n_bs": 150,
      "response_n_cs": 25,
      "response_n_ar": 70,
      "response_n_br": 10,
      "response_n_cr": 150,
      "response_block_size": 0,
      "response_st_min": 0,
      "source_sheet": "DIAG Message routing(OBD ETH)",
      "source_row": 8
    }
  ]
}
```

## Requirements

- For History work, set `output_version` to the last selected version in workbook order. Diagnostic/mapping-only work uses the explicitly selected requirement output version. Do not substitute the application/Skill version.
- Set `project_mode` to `AX`, `AY`, `AX_TO_AY`, or `AY_TO_AX` only for direct/signal History work; mapping-only work does not require it.
- Set every direct, signal, and diagnostic route `operation` to exactly `ADD` or `DELETE`.
- `routes` contains only resolved, non-LIN, direct-message route legs.
- `signal_routes` contains resolved signal route legs and may contain CAN or LIN networks.
- `diagnostic_routes` contains selected CAN diagnostic demands. Set `entry_type` to `OBD_CAN` or `OBD_ETH`; one entry represents the atomic request/response pair.
- Expand one FLZCU row with multiple selected target subnets into one manifest route per target channel.
- Use only channel names present in the template `引用数据` sheet.
- Preserve CAN IDs as hexadecimal strings.
- Preserve FLZCU message names and lengths exactly.
- Include `history_rows` and `flzcu_row` for internal validation and conversational reporting; the writer does not add them to the workbook.
- Expand one signal row with multiple target marks into one `signal_routes` entry per target network. Deduplicate by source network/message/signal plus target network/message/signal.
- Use short network codes in signal output (`CH`, `GL`, `RLHS`, and so on), not `FL_CANFD_*` or `FL_LIN_*` subnet strings.
- After resolving signal routes, run `scripts/apply_signal_invalid_values.py`. It sets top-level `signal_matrix`, copies each source signal's column-AA `Invalid Value(Hex)` into `timeout_value`, and records `timeout_value_source_sheet` and `timeout_value_source_row`. Blank AA remains blank; missing/ambiguous source matches block output. The target signal is never used for this value, and `DM_FD` is excluded.
- Compute `timeout_time` from source send type and source cycle time according to `routing-rules.md`; keep source timing evidence in the manifest.
- Include eligible project-mark and deletion routes according to `routing-rules.md`. Do not include FRZCU, AZ, cycle-message, LIN-message, or `FL_CANFD_DM` target records.
- For OBD CAN, include both request-side and response-side channel/type/length fields and both transport-parameter sets. The request channel is an explicit user input.
- For OBD ETH rows used as ordinary CAN diagnostic routes, include both request-side and response-side CAN identity, channel/type/length, and transport parameters. `OBD_ETH` is the source sheet marker, not an instruction to omit the request CAN endpoint or infer DoIP. The request channel must be resolved separately; for the user's EEA5.1 Robotaxi workbook it is `DGCAN`.
- Set BDCAN/DMCAN/DGCAN endpoint settings to Rx `STANDARD_NO_FD_CAN`, Tx `STANDARD_CAN`, Length 8. Set every other CAN endpoint to Rx/Tx `STANDARD_FD_CAN`, Length 64.
- Resolve N_As/N_Bs/N_Cs/N_Ar/N_Br/N_Cr/BlockSize/STmin from explicit user values or, when requested, consistent same-channel physical/functional examples in the supplied baseline ARXML. Never copy example values from the template.

## DoIP-to-CAN mappings

Optional `doip_can_mappings` is a list of seven-column mappings, not ADD/DELETE operations:

```json
{
  "Tester": "0x0E80",
  "logicalAddress": "0x011F",
  "RouterType": "OBD",
  "CANBus": "PTCAN",
  "RequestCanId": "0x7E0",
  "RespCanId": "0x7E8",
  "Functional": "",
  "sources": [{"sheet": "DIAG Message routing(OBD ETH)", "row": 102, "tester_column": 6, "network": "FL_CANFD_PT"}]
}
```

Functional rows use `Functional: "YES"` and `RespCanId: ""`. Uniqueness is `(logicalAddress, Tester, CANBus)`; preserve each actual Tester association, do not fill a four-Tester Cartesian product. `sources`, `doip_selection` and extraction statistics remain internal JSON metadata, not extra workbook columns. Selection and matching rules are in `doip-can-routing-rules.md`. No DBC, baseline ARXML or CanTp timing parameters are required for this mapping list.
