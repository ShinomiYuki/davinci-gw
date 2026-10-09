# Routing rules

## Scope

- Process only selected History version groups and `Routing(FLZCU)` rows whose routing type is `Message` or `Signal`.
- Allow selecting one or more version groups. Apply them in History row order; the latest selected group determines the output suffix.
- Ignore `Routing(FRZCU)` content, explicit FRZCU text, `Cycle Message Routing`, AZ, and all message routes with LIN at either end. Use AX and AY only according to the selected project mode. Signal routes may contain LIN legs.
- Ignore the target subnet column `FL_CANFD_DM`. Use only `FL_CAN_DM` for DM target routes.
- Do not generate report files or report sheets.

## Version groups

A non-empty History column B starts a version group. Following rows with an empty B belong to that version until the next non-empty B or end of sheet.

When multiple versions are selected:

1. Merge additions and modifications in History order.
2. Deduplicate identical route legs.
3. Let a later selected modification win.
4. Let a later selected deletion cancel an earlier selected addition of the same route.
5. Otherwise retain an eligible deletion as a `DELETE` row for downstream ARXML removal.

## Operation column

- Write `ADD` for an eligible History addition or modification.
- Write `DELETE` for an eligible History deletion or cancellation.
- Write the operation into the `操作类型` column on both output sheets.
- Normalize modification of a route identity as `DELETE` of the old complete key plus `ADD` of the new complete key.
- Do not guess an operation when History wording and project evidence conflict; report the route as ambiguous.

## Project-mark modes

Always ask the user to choose one mode. Never infer it from the workbook or filename.

When a project cell contains multiple marks such as `√\n×` or `×\n√`, use the last mark symbol as the current state. Treat a last `√` as active and a last `x`, `X`, or `×` as inactive. Treat blank as inactive for transition comparison.

| User choice | Script mode | Rule |
|---|---|---|
| AX列变更 | `AX` | For selected History project-mark changes affecting AX, current AX `√` means `ADD`; `x`, `×`, or blank means `DELETE`. |
| AY列变更 | `AY` | For selected History project-mark changes affecting AY, current AY `√` means `ADD`; `x`, `×`, or blank means `DELETE`. |
| AX→AY | `AX_TO_AY` | Treat AX as the old project snapshot and AY as the new snapshot. AX inactive to AY `√` means `ADD`; AX `√` to AY inactive means `DELETE`; equal states produce no project-transition row. |
| AY→AX | `AY_TO_AX` | Treat AY as the old project snapshot and AX as the new snapshot. AY inactive to AX `√` means `ADD`; AY `√` to AX inactive means `DELETE`; equal states produce no project-transition row. |

For `AX` or `AY`, do not emit rows solely because an unrelated FLZCU row currently contains `x` or blank. A selected History item must describe a project-mark change and resolve to that row. If the History item explicitly names only the other project column, ignore it for the selected same-column mode.

For transition modes, use `project_transition_candidates` from the extractor to evaluate all eligible FLZCU route rows whose AX/AY states differ. Expand the resulting operation to every allowed marked target leg. Merge identical transition and History legs by the complete route key and operation; report conflicting operations instead of guessing.

For non-project History additions or modifications, output them only when the destination project state is active: AX for `AX`/`AY_TO_AX`, AY for `AY`/`AX_TO_AY`. Keep explicit History deletions when the corresponding old project state was active. If applicability cannot be established, report it rather than writing.

## FLZCU layout

- A: signal name
- B: routing type
- C: source subnet
- E: source message name
- G: source message frame type
- H: source CAN ID
- I: source message length
- P: target message name
- Q: target message frame type
- R: target CAN ID
- S: target message length
- AB:AN: target subnet marks

For `Signal` rows additionally use:

- J: source send type
- K: source cycle time in milliseconds
- L: source signal start bit (matching evidence only)
- M: source signal length (matching evidence only)
- N: source byte order
- O: target signal name
- AO:AW: LIN target subnet marks

Target columns:

| Column | Subnet | Channel | Use |
|---|---|---|---|
| AB | FL_CANFD_DG | DGCAN | yes |
| AC | FL_CANFD_EP | EPCAN | yes |
| AD | FL_CANFD_PT | PTCAN | yes |
| AE | FL_CANFD_CH | CHCAN | yes |
| AF | FL_CANFD_IC | ICCAN | yes |
| AG | FL_CANFD_DK | DKCAN | yes |
| AH | FL_CAN_BD | BDCAN | yes |
| AI | FL_CAN_DM | DMCAN | yes |
| AJ | FL_CANFD_DM | DMCAN | no; always ignore |
| AK | FL_CANFD_LC | LCCAN | yes |
| AL | FL_CANFD_DA | DACAN | yes |
| AM | FL_CANFD_SU | SUCAN | yes |
| AN | FL_CANFD_GL | GLCAN | yes |

Signal-only LIN target columns:

| Column | Subnet | Output network | Use |
|---|---|---|---|
| AO | FL_LIN_TDL1 | TDL1 | signal only |
| AP | FL_LIN_TDL2 | TDL2 | signal only |
| AQ | FL_LIN_RLHS | RLHS | signal only |
| AR | FL_LIN_EBS | EBS | signal only |
| AS | FL_LIN_DDSP | DDSP | signal only |
| AT | FL_LIN_LSMM | LSMM | signal only |
| AU | FL_LIN_PSMM | PSMM | signal only |
| AV | FL_LIN_SRF | SRF | signal only |
| AW | FL_LIN_DLM | DLM | signal only |

Source subnet conversion:

- Convert `FL_CANFD_<CODE>` and `FL_CAN_<CODE>` to the channel in the template's `引用数据` table.
- Map both source `FL_CAN_DM` and source `FL_CANFD_DM` to `DMCAN`; the ignore rule applies only to the AJ target column.
- If the source subnet cannot be mapped to `引用数据`, do not write the route and report it as unmatched.

## Direct-routing output rules

| Output column | Value |
|---|---|
| 源网段报文名称 | FLZCU E |
| 源网段报文CANID | FLZCU H, hexadecimal text |
| 源网段报文Length | FLZCU I |
| 源网段报文类型 | `STANDARD_CAN` |
| 源网段CAN通道 | mapped from FLZCU C |
| 源网段RxIndicationUL | `PDUR` |
| 源网段报文Checksum使能 | blank |
| 源网段报文Dlc Check使能 | `Enable` |
| 目标网段报文名称 | FLZCU P |
| 目标网段报文CANID | FLZCU R, hexadecimal text |
| 目标网段报文Length | FLZCU S |
| 目标网段报文类型 | `STANDARD_CAN` for BDCAN or DMCAN; otherwise `STANDARD_FD_CAN` |
| 目标网段CAN通道 | target-column channel mapping |
| 目标网段报文Checksum使能 | blank |
| 目标网段报文PnFilter使能 | blank |
| 目标网段报文Truncation使能 | `Enable` |
| 路由Length Strategy功能选择 | `IGNORE` |
| 操作类型 | `ADD` or `DELETE` |

## Signal-routing output rules

Write one target leg per row. If one FLZCU signal row marks three target subnets, write three rows with repeated source fields. Group these rows by `(源网段, 源报文名, 源信号名)` only when later generating `ComGwMapping`.

| Output column | Value |
|---|---|
| 源网段 | short code from FLZCU C, e.g. `CH` or `RLHS` |
| 源报文名 | FLZCU E |
| 源信号名 | FLZCU A |
| 字节序 | FLZCU N |
| 超时值 | source signal's `Invalid Value(Hex)` from column AA of the customer signal matrix; blank when AA is blank |
| 超时时间 | computed below, in seconds |
| 源信号组合名 | blank unless a verified DaVinci combined name is available |
| 目标网段 | short code from the marked target subnet |
| 目标报文名 | FLZCU P |
| 目标信号名 | FLZCU O |
| 操作类型 | `ADD` or `DELETE` |

Do not use the old `目标网段统计` comma string or horizontally repeated target columns.

Configure timeout only on the source/Rx signal. For both `ADD` and `DELETE`, locate the source-network sheet `FLZCU_VCU_<源网段>` in the customer signal matrix, then match the source message name in column D and source signal name in column L. Copy column AA `Invalid Value(Hex)` to `超时值`. Matching is trimmed and case-insensitive but otherwise exact. Forward-fill merged/blank message-name cells while scanning a message block. A blank AA cell means the source signal has no invalid value and `超时值` remains blank. Missing or multiple source matches are blocking ambiguities; never read the target signal as a fallback. Exclude the `FLZCU_VCU_DM_FD` sheet.

Use source send type from FLZCU J and source cycle time from K for `超时时间`; never use target cycle time U.

- If send type does not contain `Cyclic`, leave timeout blank.
- If cycle time is blank, zero, or invalid, leave timeout blank and report the unresolved timeout input.
- If `Cycle <= 20 ms`, timeout is `500 ms`.
- If `20 ms < Cycle <= 100 ms`, timeout is `Cycle * 20 ms`.
- If `Cycle > 100 ms`, timeout is `4000 ms`.
- Store `超时时间` as seconds because it is written directly to AUTOSAR `ComTimeout`: `timeout_ms / 1000`.
- Keep all rows for the same source key consistent in byte order, timeout value, timeout time, and source combined name.

## Matching policy

Normalize CAN IDs numerically so `0x03` and `0x003` compare equal. Trim names and compare case-insensitively, but preserve original FLZCU spelling in output.

Auto-resolve only when evidence identifies a single FLZCU route row and target leg. Prefer:

1. message name + CAN ID + source subnet + target subnet;
2. message name + CAN ID + target subnet;
3. message name + uniquely stated source/target context.

Treat name-only, ID-only, conflicting IDs, and multiple candidates as ambiguous. Suggest candidates in conversation but do not write without confirmation.

For signal routes, prefer source/target signal name + source/target message ID + source subnet + target subnet. When History explicitly names a target subnet, output only that allowed marked target leg; do not expand unrelated target marks from the same FLZCU row. When History describes the whole signal row without narrowing targets, expand all allowed marked target legs. Treat conflicting source/target network direction or multiple exact signal rows as ambiguous.

Use these keys for incremental merging:

- Direct message: source message/ID/channel + target message/ID/channel.
- Signal: source network/message/signal + target network/message/signal.

Let a later selected signal deletion cancel an earlier selected addition only when the complete signal key matches.
