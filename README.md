# 带材测厚复检站

白班接管测厚设备给出的原片，质检员选定正式测次，夜班断源后按复检批号复领原字节、出处、整数微米值和首次签发回执。设备的两个周转槽可反复装片。采集员先保全每次原片，再放行对应任次。暂选可修改，正式选择固定。同槽的其他原片即使读值相同，也不能补齐正式选件。

## 运行

使用 Python 3.11 标准库。控制台在物化工程时提供完整回归入口。在项目根目录运行：

```text
python3 -B check_project.py
python3 -B -m examples.demo
```

在线迁片演示另由运行清单的 `demo` 执行：`python3 -B -m examples.demo_slot_move`。

也可直接由 Python 发现同一套测试：

```text
python3 -B -m unittest discover -s tests -t . -p 'test_*.py'
```

演示使用 `examples/shift.json` 和临时目录中的两个 SQLite 库。先暂选 X，接管 Y 并在设备成功放行后丢失回包，再往原槽装入 Z。重开后续办 Y 的原操作，先实收 Z 再正式定档 Y。两个测次的计数同为 13，在零位 2、比例 1 的规则下都应为 11 μm。Y 实收前保持待件。Y 实收后签存，设备关闭后复领同一原字节、出处和首执。

## Python 业务入口

1. `recorder.capture.open_device(path)` 和 `intake.evidence.open_station(path)` 分别打开并初始化设备库和站库。使用 `contextlib.closing` 或显式关闭连接。入口自行提交相应的数据库事务，调用时连接不得已有外层事务。重复打开保留通过业务入口录入的数据和已签存结果。
2. `measurement.policy.open_shift(station, shift_id, zero_offset, micrometers_per_count, bad_quality_mask)` 冻结规程。零位为整数，比例为正整数，质量掩码为非负整数。同参复开返回原规程，异参或非法参数抛出 `ValueError`。`review.batch.open_batch(station, batch_id, shift_id)` 在已开班下登记批次。
3. `recorder.capture.record(device, batch_id, capture_id, raw, source, slot_id=...)` 返回设备公告。指定槽忙或全部槽占满时抛出 `SlotBusy`。未指定槽时选首个可用槽。同一设备库内的测次号唯一。原编号重投须保留所属批次、原字节、出处及已指定的槽。`intake.evidence.announce_origin(station, origin)` 接收可信设备公告，`review.batch.register_candidate(station, batch_id, capture_id)` 将本批公告纳入复检候选。候选可早于实际原片登记。
4. `intake.adopt.adopt(station, device, capture_id, operation_id, method='station')` 先提交站库实际原片再放行。选择 `method='device'` 时先提交设备独立固定物，随后可用 `intake.adopt.receive_retained(station, device, capture_id)` 按测次取回并实收。已有可信公告的实际原片也可经 `intake.receipts.receive_original(station, original)` 提交。来源或字节冲突抛出 `ValueError`。保全、实收和操作绑定都是可查的持久状态。保全失败时槽继续占用，采集员可重投。设备回包丢失时，`intake.resume.resume_release(station, device, operation_id)` 查回原放行结局，未发现结局时返回 `None`。传入 `retry=True` 可用原绑定和可验证保全重发。演示通过 `lose_reply=True` 在设备提交后确定性地模拟失答。
5. `review.preview.preview(station, batch_id, capture_id)` 保存暂选并返回已有测厚判定。`review.decision.decide_batch(station, batch_id, capture_id)` 固定正式选择，返回 `pending`、`ready` 或附原因的 `rejected`。非本批候选、终审后暂选和同批改选抛出 `ValueError`。同选择重投复用定档，并按目前的实际实收核定。
6. `delivery.publish.publish(station, batch_id)` 使用已经提交的正式定档和实际实收。可显式传入 `selected_capture_id` 或 `receipt` 核对同一依据。待件返回 `None`，错件抛出 `ValueError`，质检不合格抛出 `RejectedMeasurement`。签存失败由 SQLite 抛错，成果、首执和班账均未提交，定档与实收可用于重试。已签发的同件重投返回原快照。
7. `delivery.claim.claim(station, batch_id)` 按原批号读取成果，已知未签批返回 `None`。`delivery.shift.shift_ledger(station, shift_id)` 按签存顺序列出批号、读值和出处。已开空班返回空表，未知批或班抛出 `KeyError`。两项读取只需要站库。

## 在线迁片

迁片测次的调用约定见 `SLOT_MOVE.md`。`recorder.capture.record(..., movable=True)` 录入迁片测次，`CaptureOrigin.movable` 记住该标记。`recorder.capture.move_capture(device, operation_id, capture_id, target_slot, *, before_commit=None)` 只改设备库，把尚未放行的迁片搬到另一空槽并返回 `MoveOutcome`；忙槽抛出 `recorder.slots.SlotBusy`，提交前故障钩子抛错则原槽不变且不消耗迁片次数。`recorder.capture.query_move(device, operation_id)` 查已提交的搬移结局，同号同参重投复领原结果，异参或二次成功搬移抛出 `ValueError`。

`recorder.capture.read_live_packet(device, capture_id, *, seal)` 从迁片测次当前实际占用的槽取读并封包，已无在线原片返回 `None`。`recorder.packet.encode_live_packet` 与 `decode_live_packet` 定义封包正文的编码，`seal`/`verify` 由可信端口提供。`intake.receipts.receive_live_packet(station, capture_id, packet, *, verify)` 只依赖站库、读包与验包端口完成实收，读址 `read_location` 为 `(slot_id, generation)` 并固定为首次有效实收；同一测次的其他真实读包复领该实收，篡改、错绑与字节冲突抛出 `ValueError`。旧裸件实收、旧设备固定凭据和普通放行都不能接收或放行迁片测次。

`intake.adopt.release_movable(station, device, capture_id, operation_id, *, lose_reply=False)` 凭已提交的合格读包实收放行，只清退该测次的现槽，结果沿用 `ReleaseOutcome`。放行失答抛出 `ReleaseReplyLost`，`intake.resume.resume_release` 按原放行号续办，同参复领原结局。迁片成果的 `read_location` 记录首次有效实收的槽与任次，`piece.original.origin` 仍提供初录出处。

站库入口接收可信设备端口的公告，并按公告核验后续传送的出处、任次和字节摘要。底层槽分配及清槽函数由设备事务调用。低层签存函数接收业务发布入口核定过的快照。设备固定凭据由设备放行端核对，站库凭据由接管端的可信实收校验端口核对。两类凭据都必须在清槽前对应已持久的同一原片。

## 原片及读值

原片是保留原样的字节。测厚解码采用 UTF-8 JSON 对象，`quality` 为非负整数，`count` 为整数、空值或缺失。格式或字段类型非法时记录解码原因。质检先核对 `quality` 与当班坏位掩码，再区分缺计数和空计数。通过后计算 `(count - zero_offset) × micrometers_per_count`，结果为整数微米，零计数有效。规程和实收、成果中的读值以十进制整数字符串持久化。可信坏片同样保留原片和出处，并可正常办理放行。

设备库保存槽任次、测次公告、固定原片、搬移结局和设备放行结局。站库保存规程、公告、实收（含首次读址）、放行绑定、候选、正式定档及成果。回归通过真实 SQLite 事务检查保全失败与签存回滚，通过重开验证旧操作和离线复领取得同一结果；迁片联测覆盖迟包、错绑、篡改、失答续办、两槽复用后领取 11 μm。
