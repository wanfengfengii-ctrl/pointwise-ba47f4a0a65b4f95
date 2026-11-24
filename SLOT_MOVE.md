# 在线迁片调用约定

设备库和站库是两个独立的 SQLite 库。开班、建批、可信公告登记、候选登记和定档按 README 办理。槽号为 `s1` 和 `s2`，任次由设备分配。同一测次只有一份初录公告。初录槽及任次、批次、实写出处、摘要和原字节在搬移后仍代表同一物理测次。

## 设备录入、移动和读包

- `recorder.capture.record(..., movable=True)` 录入迁片测次。`CaptureOrigin` 携带布尔属性 `movable`，旧构造和旧录入默认为 `False`。录入或登记公告重投须遵守原有原件约束，迁片标记也须一致。站库继续用 `intake.evidence.announce_origin(station, origin)` 接受可信设备端口的公告。
- `recorder.capture.move_capture(device, operation_id, capture_id, target_slot, *, before_commit=None)` 把尚未放行的迁片测次搬到另一空槽，只需要设备库。结果提供 `operation_id`、`capture_id`、`from_slot`、`from_generation`、`to_slot`、`to_generation`。目标使用自身的新任次，成功后原槽可立即录新片。忙槽抛出 `recorder.slots.SlotBusy`。`before_commit` 是可选无参故障钩子，在首次搬移的写入与提交之间调用，抛错须原样传播。忙槽或提交失败不产生搬移结局，也不消耗成功搬移次数。
- `recorder.capture.query_move(device, operation_id)` 查设备已提交的原结果，未知返回 `None`。同号同测次同目标重投返回原结果，即使两个槽已复用。异参或再次成功搬移的请求抛出 `ValueError`。
- `recorder.capture.read_live_packet(device, capture_id, *, seal)` 从迁片测次当前实际占用的槽取读，已无在线原片时返回 `None`。`seal` 由可信设备端口提供，接受不透明 `bytes` 并返回封包。封包须让站库验证同一次读取的原公告、原字节和实际读出槽任次。读址来自实际读出的占槽，搬移或复用发生后，已发封包中的读址仍指向发包所据的那次实读。封包正文编码由实现决定。

## 站端实收、放行与领取

- `intake.receipts.receive_live_packet(station, capture_id, packet, *, verify)` 接收指定迁片测次的读包。`verify` 接受封包并返回经认证的正文 `bytes`，验包失败抛出 `ValueError`。实收只依赖站库的可信公告、读包和预配验包端口。合法迟包仍可用，即使读出槽已有其他测次。篡改、错绑和冲突抛出 `ValueError`，即使原字节一样或目标已有实收也须拒绝。实收原字节、原定标结果和首次实际读址一起持久化。该测次的其他真实读包重投复领同一实收。返回 `CaptureReceipt`，并通过 `find_receipt` 查得同样的结果。两者的 `read_location` 为 `(slot_id, generation)`。读包完好但测厚判定不合格时沿用原来的实收和禁签规则。
- `intake.adopt.release_movable(station, device, capture_id, operation_id, *, lose_reply=False)` 凭该测次已提交的合格读包实收放行。这里的合格指来源与原件核验通过，测厚判定继续由原规程负责。结果沿用 `ReleaseOutcome`，其中 `slot_id` 和 `generation` 指实际清退的占槽。即使实收基于搬移前的读包，放行仍只清退该测次的现槽。无已提交实收或同一放行号绑定其他测次时拒绝。旧裸件实收须拒绝迁片测次，旧设备固定凭据和普通放行也不能绕过迁片实收。
- `lose_reply=True` 沿用 `ReleaseReplyLost`，用于模拟设备已提交但站端未收答复。`recorder.release.query_release` 可查设备已提交的清退结果。`intake.resume.resume_release` 按原放行号查询结局，`retry=True` 可沿原绑定续办。同号同参再次放行也须复领同一结果，不能清退后来装入的原件。搬移与放行的清退对象以设备实际提交的先后为准。
- 继续用 `delivery.publish.publish` 签存及 `delivery.claim.claim` 按原批号领取。迁片成果增加 `read_location`，指首次有效实收的槽与任次。`piece.original.origin` 仍提供初录出处，`piece.original.raw`、`piece.micrometers` 和 `piece.first_receipt` 按旧语义领取。关掉设备、重开站库后也能复领，普通实收和旧成果无需填写读址。

## 联调样例

`examples.slot_move_fixture.SAMPLE` 提供班次规程、X/Z 的原字节和不同实写出处。用 `LocalPort` 为设备和站库各建一个使用相同测试密钥的实例，分别把 `seal` 和 `verify` 传到上述入口。调用方持有封包时可用 `HeldFrames` 控制送达顺序。用 `fail_before_commit` 注入搬移失败，用 `drop_reply` 丢弃成功搬移的答复。放行失答用对应入口的 `lose_reply`。封包正文、库表和放行资格表示由实现决定。

在干净设备上先录 X 到 s1，登记候选，读出封包后暂不送达。关闭站库再搬到 s2，s1 录入同字节的 Z。此时可分别在线读到 s2 的 X 和 s1 的 Z。重开站库后先验收 X 的旧包，首次实收读址应为 `('s1', 1)`。另一次运行可先交 s2 的包再交旧包，首次实收应固定为 `('s2', 1)`。把 X 的真实包挂到已登记的 Z，或改动包中的内容后交回，均须拒绝。未提交实收时，包括尝试旧入口在内，都不能放行 X。

用原号重查或重投已提交但失答的搬移。X 实收后放行应腾出 s2。放行提交后丢掉回包，随后在 s2 装入 W，重开两库再续办原号，应领回对 X 的清退结果，s1 的 Z 和 s2 的 W 都仍可在线读。按原定档签出 X，关掉设备再重开站库。按 `B-MOVE` 领取的原字节为样例原码，初录槽为 s1 第一任次，实收读址取决于首次有效交件，读值为 11 μm，首执与签存时相同。用已占目标槽和提交前失败另验搬移原槽保全。

## 运行清单

项目根目录的 `.pointwise-runtime.json` 继续使用第 1 版 CLI 清单，`version` 为 `1`，`kind` 为 `cli`。`setup` 是准备命令列表，每条使用参数数组。`verify` 和 `demo` 各接一个参数数组。核验入口要运行全部原回归及迁片联测，断言失败以非零退出。演示入口实际执行上面的迟包、放行和停源领取，展示初录出处、首次实收读址、读值和首执。母版演示仍可按 README 独立运行。
