# Feishu AI Chain Runbook

This note records the working context for continuing the AI industry-chain Feishu table from another computer.

## Links

- Feishu wiki / spreadsheet entry: https://my.feishu.cn/wiki/UxDvwwezLihFS1kVmhHcqwypnQc
- New Feishu wiki / spreadsheet entry: https://my.feishu.cn/wiki/DlRmwrJOgiWIVhkWZtsc6h0Bnnd
- GitHub repository: https://github.com/leejiazhi88-gif/BigA-feishu-road
- Current spreadsheet token: `ElgysBB1dhMXPCtZKzncF4Denud`
- New spreadsheet token: `UJJesUWvehxTbNtNpV2cHuDcnIc`
- Recent working sheets:
  - `2026-06-05`
  - `2026-06-09`
  - `2026-06-15`
  - `2026-06-17`
- Marker sheet: `颜色标记`

## Local Setup On A New Computer

Clone the repository:

```bash
git clone git@github.com:leejiazhi88-gif/BigA-feishu-road.git
cd BigA-feishu-road
```

Create a Python environment and install the runtime packages:

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install pandas numpy tushare
```

If SSH push is not configured yet, add an SSH key to GitHub:

```bash
ssh-keygen -t ed25519 -C "BigA-feishu-road"
cat ~/.ssh/id_ed25519.pub
```

Add the printed public key at:

```text
https://github.com/settings/keys
```

Then verify:

```bash
ssh -T git@github.com
git remote set-url origin git@github.com:leejiazhi88-gif/BigA-feishu-road.git
```

## Secrets

Do not commit real tokens or Feishu secrets to GitHub.

Create a local `.env` file, for example:

```bash
cat > .env <<'EOF'
FEISHU_APP_ID=your_feishu_app_id
FEISHU_APP_SECRET=your_feishu_app_secret
EOF
```

Set the Tushare token only in the shell:

```bash
export TUSHARE_TOKEN="your_tushare_token"
```

On the original Mac, the Feishu env file used by default was:

```text
/Users/fuguiplus/Documents/Codex/2026-04-30/new-chat/xiaoniuma-feishu/.env
```

On another computer, prefer passing `--feishu-env .env`.

## Refresh Feishu

Create or refresh a dated sheet:

```bash
export TUSHARE_TOKEN="your_tushare_token"
.venv/bin/python scripts/build_ai_chain_feishu.py \
  --wiki-url "https://my.feishu.cn/wiki/UxDvwwezLihFS1kVmhHcqwypnQc" \
  --sheet-title "$(date +%F)" \
  --feishu-env .env \
  --refresh
```

Refresh a specific sheet title:

```bash
export TUSHARE_TOKEN="your_tushare_token"
.venv/bin/python scripts/build_ai_chain_feishu.py \
  --wiki-url "https://my.feishu.cn/wiki/UxDvwwezLihFS1kVmhHcqwypnQc" \
  --sheet-title "2026-06-17" \
  --feishu-env .env \
  --refresh
```

Refresh the newer A-share-only watchlist from the manually curated `2026-07-30` source sheet:

```bash
export TUSHARE_TOKEN="your_tushare_token"
.venv/bin/python scripts/build_ai_chain_feishu.py \
  --wiki-url "https://my.feishu.cn/wiki/DlRmwrJOgiWIVhkWZtsc6h0Bnnd" \
  --sheet-title "$(date +%F)" \
  --source-spreadsheet-token "ElgysBB1dhMXPCtZKzncF4Denud" \
  --source-sheet-id "3AJm92" \
  --drop-hk \
  --feishu-env .env \
  --refresh
```

For this newer watchlist, the source of truth for stock membership is the old `2026-07-30` sheet (`3AJm92`): copy exactly that stock list, then remove all HK stocks. Do not add extra white-chip, dividend, insurance, or HK rows unless the user explicitly edits the source list.

If Tushare HK endpoints hit rate limits, A-share data can still refresh normally. HK rows may have partial blanks until the HK quota window resets.

## Current Table Conventions

- A column stock-name colors are controlled by the `颜色标记` sheet:
  - Yellow: `我持有的`
  - Green: `我想买的`
  - Blue: `我关注的`
- Matched stock names should be bold.
- Unmatched stock-name colors should be preserved, not cleared.
- Q:T are net-profit YoY percentage columns:
  - `<= 0`: red font
  - `>= 10% and < 30%`: blue font
  - `>= 30%`: green font
  - `0% to 10%`: default black font
- AE, currently `2026Q1净利润(亿元)`, compares actual profit with previous-quarter forecast:
  - Actual within +/-10% of forecast: blue background
  - Actual more than 10% above forecast: green background
  - Actual more than 10% below forecast: red background
- H:G style notes from prior edits:
  - G/H should not carry extra manual color.
  - Market cap should have no decimals.
  - M:P should keep one decimal.
- Current table includes `股息率%`, sourced from Tushare `daily_basic.dv_ttm`.
- `股息率%` must be the last column and should be written as a fraction for spreadsheet percentage formatting; for example Tushare `dv_ttm=5.2` should be written as `0.052`.
- The newer watchlist excludes all HK stocks.

## Main Script

Primary script:

```text
scripts/build_ai_chain_feishu.py
```

Important arguments:

```text
--wiki-url             Feishu wiki URL, defaults to the current AI chain wiki.
--spreadsheet-token    Direct spreadsheet token if not using wiki URL.
--sheet-title          Create or update this sheet title.
--feishu-env           Local Feishu app credential file.
--start-date           Market data start date, default 20240501.
--end-date             Market data end date, default today.
--refresh              Force refresh cached raw data.
--pause                Pause between stock requests.
--max-rows             Max table rows, default 199.
```

The script also exports the latest built table to:

```text
exports/ai_chain/ai_chain_stocks.csv
```

## Git Workflow

After each Feishu update, commit code changes and push:

```bash
git status -sb
git add scripts/build_ai_chain_feishu.py docs/FEISHU_AI_CHAIN_RUNBOOK.md
git commit -m "Update Feishu AI chain pipeline"
git push
```

Do not commit generated raw market caches unless they are intentionally needed for reproducibility.

## Standard Final Reply

After finishing a refresh, send both links back to the user:

```text
飞书表格：https://my.feishu.cn/wiki/UxDvwwezLihFS1kVmhHcqwypnQc
GitHub 仓库：https://github.com/leejiazhi88-gif/BigA-feishu-road
```
