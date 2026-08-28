# 微信个人号 AI 自动回复（WeChat Auto Reply）

微信 4.x Windows 客户端个人号 AI 自动回复机器人：别人给你的微信发私聊消息 → 自动调用 AI 生成回复 → 自动发出。回复风格通过 System Prompt 自定义，像真人聊天。

> ⚠️ **免责声明**：本项目基于 UI 自动化（模拟人工操作）实现，仅供个人学习研究。自动化操作个人微信账号存在被平台限制的风险，请自行评估并低调使用。勿用于营销群发等高频场景。

## 原理

- **收消息**：读取微信 4.x 本地加密数据库（SQLCipher），轮询会话表发现新消息 —— 不注入、不 Hook、不逆向协议
- **AI 回复**：调用任意 OpenAI 兼容 API（默认配置 OpenRouter 免费模型 `minimax/minimax-m3:free`）
- **发消息**：Windows UI 自动化定位微信窗口 + 模拟键盘输入发送
- 依赖开源库 [wechatauto](https://github.com/fanyuantaier/wechatauto-replica)（Apache-2.0）

## 环境要求

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows 10 / 11 |
| 微信客户端 | **4.1.12+**（新版微信，Weixin.exe，非 3.9） |
| Python | 3.10+ |
| AI | 任意 OpenAI 兼容 API 的 Key（OpenRouter 有免费模型） |

## 快速开始

```bash
# 1. 克隆并创建虚拟环境
git clone https://github.com/qing1989620/weixinchat-auto.git
cd weixinchat-auto
python -m venv .venv
.venv\Scripts\python.exe -m pip install wechatauto-replica openai

# 2. 复制配置文件并填入你的 API Key
copy config.example.json config.json
# 编辑 config.json，填入 api_key

# 3. 登录 PC 微信（保持窗口打开，不要最小化到托盘）

# 4. 双击 start_wxauto.bat 启动，或命令行：
.venv\Scripts\python.exe -X utf8 wechat_ai_reply.py
```

## 配置说明

所有配置集中在 `config.json`：

```json
{
  "provider": "deepseek",
  "providers": {
    "deepseek": {
      "api_key": "你的Key",
      "base_url": "https://openrouter.ai/api/v1",
      "model": "minimax/minimax-m3:free",
      "prompt": "你是运行在我私人微信上的 AI 助手……"
    }
  }
}
```

| 字段 | 说明 |
|---|---|
| `api_key` | OpenAI 兼容 API 的 Key |
| `base_url` | API 地址（OpenRouter / DeepSeek / 中转站均可） |
| `model` | 模型名（免费模型推荐 `minimax/minimax-m3:free`） |
| `prompt` | **AI 人设**，改这里就能改性格，改完重启生效 |

### 行为参数

在 `wechat_ai_reply.py` 顶部：

```python
WHITELIST = []          # 只回复指定联系人（留空 = 所有私聊）
IGNORE_GROUPS = True    # 群聊不自动回（强烈建议保持 True）
REPLY_DELAY = (3, 8)    # 回复前随机延迟（秒），模拟真人
COOLDOWN = 5            # 同一联系人两次回复最小间隔
```

## 降险措施（内置）

- ✅ 群聊默认不自动回复
- ✅ 不响应自己发的消息、系统号、公众号
- ✅ 回复前 3~8 秒随机延迟 + 同一联系人 5 秒冷却
- ✅ 只处理文本消息，图片/文件/语音不触发
- ✅ 运行日志写入 `wxreply.log`，可随时排查

## 常见问题

| 问题 | 解决 |
|---|---|
| 提示"未找到微信主窗口" | PC 微信未登录或窗口被关，重新登录并保持窗口打开 |
| 提示"数据库无可用密钥" | 微信重新登录后重启脚本（密钥在微信进程内存中） |
| 消息收到了但没回复 | 查看 `wxreply.log`，确认不是群聊/系统号/图片消息 |
| AI 回复很慢 | 免费模型限流，换 `config.json` 里的 `model` |
| 想改 AI 性格 | 改 `config.json` 的 `prompt` 字段，重启脚本 |

## 文件结构

```
├── wechat_ai_reply.py   # 主程序（收消息→AI→发消息）
├── start_wxauto.bat     # Windows 一键启动
├── config.example.json  # 配置模板（复制为 config.json 使用）
└── README.md
```

## 致谢

- [wechatauto-replica](https://github.com/fanyuantaier/wechatauto-replica) —— 微信 4.x 客户端自动化
- [OpenRouter](https://openrouter.ai) —— 免费模型 API
