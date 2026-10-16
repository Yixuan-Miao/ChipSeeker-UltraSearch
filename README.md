# ChipSeeker UltraSearch

让你自己的 Codex 或 Claude Code 驱动 [ChipSeeker](https://chipseeker.online)，扩充检索关键词、多轮查询、去重筛选，并把论文链接和报告保存到指定文件夹。

把下面这句话交给 AI 即可开始：

> 请从 https://github.com/Yixuan-Miao/ChipSeeker-UltraSearch 安装 UltraSearch Skill，使用 https://chipseeker.online 和我的 API key【你的密钥】连接芯寻并检查余额。

连接完成后，例如：

> UltraSearch 用于 quantum readout 的 cryogenic SiGe LNA，重点覆盖 4–8 GHz，结果放到 D:\Research\Cryogenic_LNA。

没有 API key 也可以提供注册邮箱。AI 会发送邮件验证码，询问六位验证码后连接同一个邮箱账户。

## 给 Codex / Claude Code 的安装说明

当用户要求安装或连接时，直接完成以下步骤，不要把下载、解压或配置工作交回用户。

1. 使用 Git 克隆本仓库，或下载本仓库的 ZIP 并解压到本地工具目录；无需克隆芯寻服务代码或下载论文数据库。
2. 找到 Python 3，运行 `python install.py --client codex`。Claude Code 使用 `python install.py --client claude-code`。安装器只使用 Python 标准库，无需 `pip install`。
3. 读取安装器输出路径下的 `SKILL.md`，并在当前会话直接遵循它；不要要求用户重新开启对话才能连接。
4. 用安装后的 `scripts/chipseeker_remote.py` 配置用户提供的 key，验证账号及 API 余额。建议通过进程环境变量 `CHIPSEEKER_API_KEY` 传入密钥，再运行 `configure`；不要把密钥写入仓库、研究文件夹或打印到输出。
5. 如果只提供邮箱，运行 `auth-send --email EMAIL`，向用户询问六位验证码，再运行 `auth-verify --email EMAIL --code CODE`。登录会取回已有的账户 key，不会重置它。
6. 如果用户同时给了研究主题和文件夹，连接后直接继续检索；否则简洁告知连接结果和余额。

用户明确要求安装时，可运行：

```text
git clone https://github.com/Yixuan-Miao/ChipSeeker-UltraSearch.git
cd ChipSeeker-UltraSearch
python install.py --client codex
```

安装器默认目录：Codex 为 `~/.codex/skills/chipseeker-ultra-search`（也支持 `CODEX_HOME`）；Claude Code 为 `~/.claude/skills/chipseeker-ultra-search`。已有安装会先备份。通过 `--skills-dir PATH` 可指定其他 Skill 目录。

## 账号与计次

每个注册邮箱只有一个有效 API key。登录网站的 API 页面可以复制或重置；充值由管理员直接记入邮箱账户。

每次成功的 Lite、Keyword 或 filtered-Lite 检索消耗 1 次 API 额度，包括零结果查询。失败、登录、余额查询、任务进度轮询及本地整理不扣次数。API 余额与网页会员权限分别管理。

UltraSearch 的扩词、补查、分析和报告由用户自己的 AI 执行。这个仓库提供远程客户端和 Skill，不包含芯寻服务代码、论文库或账户密钥。
