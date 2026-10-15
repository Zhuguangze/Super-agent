# 03: 两级队列 + 排空模式

**What to build:** steering(运行中插话)与 follow-up(结束后续跑)分离成两个独立队列,各自按语义消费。引入 `all`/`one-at-a-time` 两种排空模式,控制每轮注入的消息条数——`one-at-a-time` 是默认,避免一轮塞进多条插话。

**Blocked by:** 02 (双层循环)

**Status:** resolved

- [x] steering 与 follow-up 分离(经双层循环的两个 drain 点,见 Answer 偏离说明)
- [x] 排空模式 `all` / `one-at-a-time` 可配置,`one-at-a-time` 为默认
- [x] 测试覆盖:插话与续跑按各自队列消费、`one-at-a-time` 下每轮只注入一条

## Answer

完成。`_drain` 加 `mode` 参数(`all` 一次性取全部 / `one-at-a-time` 每轮取一条,默认后者),`agent_loop` 加 `queue_mode` 参数透传给两处 drain(内层 steer、外层 follow-up)。新增 2 个测试。

**偏离说明**:验收标准里的"两个独立队列"未按字面实现——Super-agent 的 WS 层在消息到达时无法预知它是 steer 还是 follow-up(两者都发生在"run 活跃期间",区分靠 loop 内 drain 时机)。实际落地是"单队列 + 双层循环的两个 drain 点"(Ticket 02 已实现语义分离)+ 排空模式。显式的双队列需要 WS 层新增 settle 状态机,那是更大的改动,不在本 ticket 范围。

