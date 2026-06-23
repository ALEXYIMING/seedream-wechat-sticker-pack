---
name: seedream-wechat-sticker-pack
description: |
  用火山方舟 Seedream 生图能力一键完成微信表情开放平台整套上架物料。每当用户说“用 Seedream 做一套微信表情”“生成微信表情全套物料”“表情包上架”“16/24 张表情 + 横幅封面图标”“把这些提示词做成微信表情包”等，都应优先使用本 skill。它会编排 byted-ark-seedream-skill 生成无水印原图，并在当前项目目录下输出原始目录、主表情240、配套图、文案、打包 zip。不要用于单张图片预览、非微信表情平台、或用户只想普通生图的场景。
compatibility: Requires Node.js 18+ for byted-ark-seedream-skill and Python 3 with Pillow for local image processing.
---

# Seedream 微信表情包全套物料编排

这个 skill 是组合型工作流：Seedream 只负责生图，本 skill 负责编排 16/24 张主表情、微信规格处理、配套图、本地文案和打包。

## 固定原则

- 使用 `byted-ark-seedream-skill/scripts/generate.js` 作为唯一生图引擎。
- 默认无水印：调用 Seedream 时传 `--watermark false`。
- 默认输出在当前项目目录，不放桌面、不放用户主目录。
- 原始生成图统一平铺到 `./原始目录/`，不按日期建目录，不一张图一个文件夹。
- 后处理输出固定为：

```text
<project>/
├── 原始目录/          # Seedream 原图，平铺
├── 主表情240/         # 16/24 张 240×240 PNG
├── 配套图/            # 5 张微信配套图
├── 文案/              # album.md / 文案.txt
└── 打包/              # zip
```

## 什么时候先问用户

如果用户只说“做一套微信表情”但没有主题或表情列表，先问：

1. 主题/风格是什么？
2. 是否已有 16/24 条提示词？如果没有，是否需要我补齐？
3. 表情是“文字图保留背景”还是“角色图透明底”？

如果用户已经给了完整 16/24 条提示词，直接进入执行前确认：先生成样张，确认风格后再批量生成。

## 推荐执行策略

### 阶段 0：样张

对新风格先生成 1-3 张样张，不要直接批量 16/24 张。重点检查：

- 中文文字是否正确、可读。
- 240×240 缩小后是否仍清楚。
- 是否无水印。
- 风格是否统一。

### 阶段 1：准备 prompt 文件

把用户给的 16/24 条整理为 JSON：

```json
[
  {"id": "01", "title": "你一出现我就掉线", "prompt": "完整提示词..."},
  {"id": "02", "title": "...", "prompt": "..."}
]
```

保存到项目目录，例如：`prompts_8bit_love.json`。

### 阶段 2：批量生成 + 处理 + 打包

运行脚本：

```bash
python3 /Users/alexyiming/.claude/skills/seedream-wechat-sticker-pack/scripts/seedream_wechat_pack.py \
  --prompt-file ./prompts_8bit_love.json \
  --theme "8Bit 土味情话" \
  --album-name "像素情话" \
  --intro "一套复古街机像素风土味情话表情包，高对比撞色，直球心动。" \
  --tip "喜欢就给点电" \
  --transparent-mode keep
```

常用参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--project-dir` | 当前目录 | 所有输出目录所在项目目录 |
| `--prompt-file` | 必填 | JSON 或 Markdown 提示词文件 |
| `--theme` | 自动 | 主题 |
| `--album-name` | 自动 | 专辑名，建议 ≤8 字 |
| `--intro` | 自动 | 专辑介绍，≤80 字 |
| `--tip` | 自动 | 赞赏引导语 |
| `--skip-generate` | false | 跳过 Seedream，直接处理 `原始目录/` 已有图 |
| `--transparent-mode` | `keep` | `keep` 保留背景；`remove` 去纯色背景；`auto` 自动判断 |
| `--optimize` | true | 是否让 Seedream 优化提示词 |

文字型像素表情建议 `--transparent-mode keep`，因为强背景和撞色是设计的一部分。角色型白底/纯色底表情默认优先用 `--transparent-mode remove`，让最终 `主表情240/` 输出成为最佳 240×240 透明底 PNG；不确定时用 `auto`。

## 产物规格

### 主表情

```text
主表情240/
├── 01_标题.png
├── 02_标题.png
└── ...
```

- 240×240 PNG。
- 不拉伸，中心裁切为正方形后缩放。
- 文字型保留背景；角色型按参数透明化。
- 用户要求“240×240 去除背景透明处理”“透明底”时，必须使用 `--transparent-mode remove`，并以 `主表情240/` 的透明 PNG 作为最终主表情。

### 配套图

```text
配套图/
├── banner_750x400.png
├── cover_240.png
├── icon_50.png
├── reward_guide_750x560.png
└── reward_thanks_750x750.png
```

配套图默认从 16/24 张主表情本地排版生成，避免 AI 重画导致中文错字。

### 文案

```text
文案/
├── album.md
└── 文案.txt
```

包含：专辑名称、介绍、赞赏引导语、赞赏致谢语、每张表情名称。

### 打包

```text
打包/<album-name>_微信表情物料.zip
```

zip 内包含 `主表情240/`、`配套图/`、`文案/` 三类产物。

## 质量检查

执行完成后报告：

- 原图数量、主表情数量、配套图数量。
- 是否有失败项。
- zip 路径。
- 关键本地路径。

如果生成过程中中文错字、主体太小、风格偏差，单张重生后再重新运行 `--skip-generate` 做后处理和打包。

## 文件

- `scripts/seedream_wechat_pack.py` — 组合编排脚本。
- `references/workflow.md` — 目录、执行流程和故障处理细节。
