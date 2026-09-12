# model-fusion 话术清单

## 初始化开场

进入模型融合初始化。共：探测列表 → 表单填写 → 确认写入。  
请先看探测结果，再按表单一次性回复。

## 探测全失败

未检测到可用的 API key，或探测请求全部失败。  
请检查环境变量（如 `DEEPSEEK_API_KEY`、`OPENROUTER_API_KEY`）是否已配置，网络是否可用。  
配好 key 后可再说「模型融合初始化」重试。

## 表单解析失败

解析失败，未写入配置。常见原因：
- `judge` / `workers` 未填或不在可用列表里
- 采样次数必须是 1、2 或 3
- 策略必须是 A / B / C

请按示例格式重新一次性回复，只改空即可。

## 沿用确认

沿用配置 **{profile}**：{strategy_label}，workers {workers_summary}，judge {judge}。  
继续吗？

## 自定义后是否保存

本次自定义是否写入为默认？[否（仅本次）/ 是，保存为 default / 另存为新 profile]

## hard_only 跳过

这是简单任务，未触发多模型融合。如需强制融合请说「强制 /fuse」。

## 融合路数不足

成功候选 {ok} 路，少于期望的 {min_success} 路。  
{fallback_note}

## Judge 失败

各模型候选已跑完，但 judge 调用失败。  
已保存候选到 `{run_path}`。可重试融合，或让我把候选要点整理成对照表。

## 全部 worker 失败

所有 worker 调用均失败。请检查 key、余额与网络。  
未生成终稿。

## 策略 C 解析确认

解析结果：
- 策略：{strategy}（A=等权 / B=主方案+补充）
- judge：{judge}
- workers：{workers}
- 采样：{samples}
- 温度：{temperature}

确认写入 profile **{profile}** 后开始融合。

## 无法解析 C

我无法把你的描述映射成合法策略（只能是 A 或 B）。  
改用填空模板回复一次即可：

```text
策略: B
主方案: <模型, 模型>
补充: <模型可空>
judge: <模型>
采样: <模型>=<1-3>, ...
温度: 默认
profile: default
```

## 成功回呈（模板）

本次融合
- profile: {profile}
- strategy: {strategy}
- judge: {judge}
- workers: {workers_summary}
- 成功/失败: {ok}/{total}

【最终答复】
{final}
