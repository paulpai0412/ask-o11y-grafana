# Ask O11y：自主分析師與受眾解釋

> 本文件保留第一批指引變更的驗證快照。後續能力重選與授權實作、最新限制和統一部署前檢查，見 [有界自主分析](ask-o11y-bounded-autonomy.md)。

## 第一批範圍與狀態（2026-09-07）

使用者授權依方案調整；本批實作核心責任、既有 Skill 及受眾解釋，未部署。
使用者提供決策問題與授權，不必提供演算法／分析程序。LLM 提出可驗證的分析策略；
方法是否執行仍取決於 exact Analysis Preview 的確認與既有 host gates。

不是更換 agent、增加模型、安裝 GitHub skill、固定 DAG、keyword router、模型排名、
通用統計門檻、固定角色報表或新的權限系統。現有 Skill selector 上限仍為 2，沒有
增加重選 runtime；按需重新選能力與範圍型授權是後續項目，不在本批假裝完成。

本批由 main 直接完成。Team 預檢在 `team.docs missing skill: docs-generator` 失敗，
因此未啟動任何 child、未補裝 skill／放寬設定；沒有獨立 child review。

## 實作位置

- `patches/ask-o11y-autonomous-analyst.patch`：在現有 patch stack 最後套用；更新
  `pkg/plugin/prompt_defaults.go`、`data-understanding`、`ml-method-selection`，
  並加入兩份 Go regression tests。
- `scripts/build-install-ask-o11y.sh`：只增加上述 patch 的 apply 行；本輪未執行 installer。
- `scripts/configure-ask-o11y-workflow-tools.py`：同步更新設定 payload 的 custom prompt。
  **此路徑會覆蓋 Go 預設 prompt，不能只更新 Go。** 既有自訂 prompt 不會自動被新版取代。
- `scripts/check-ask-o11y-autonomous-analyst.py`：離線重建完整 installer stack，測試
  真正的 PromptRegistry、embedded Skill discovery/selection 與設定 payload。
- `CONTEXT.md`：只新增 glossary；未將本方案塞入 glossary，亦未重寫既有歷史架構段落。

歷史 skill patches 與既有 NLAP patch 保持原樣，避免破壞依序套用的 context 及其他
未提交修改；最終 overlay 移除舊的選法配方。因此舊 skill-patch checker 成功只證明
歷史輸入有效，**新版 checker 才驗證最終有效內容**。實作在隔離 local clone，未改寫
共用 `.scratch/ask-o11y-release-build`，亦未更動目前安裝的插件。

## 行為契約

- 未指定演算法不是意圖不明。從 metadata 取得 factual inputs；LLM 自行提出方法、
  supported split、features 與 validation。只有未解決的業務含義、目標衝突、成本／
  工程界線或授權問題才詢問。欄位／target 語义不靠名字、順序或量值猜測。
- 沒有「必須先 profiling 再 ML」階梯，也不需要使用者說出 ML 才能提出有用的方法。
  Profiling/model/dashboard 不是完成條件；應連回決策問題並依證據決定繼續或停止。
- 方法知識改為適用條件、假設、驗證與限制。移除 universal TimeSeriesSplit、固定
  class-weight→SMOTE 順序、樣本量換模型閾值、預設高 recall、通用 PSI/Cpk 門檻，
  以及 SHAP 選欄後當成預先指定檢定的配方。現有 deterministic executor 限制保留，
  明確區分「工具支援邊界」與「統計方法偏好」。
- 明確分開探索發現與確認性證據，相關／SHAP 不等於因果。資料不足是有效的受限
  結果，不能靠模型、圖表或捏造效益掩飾。
- 受眾使用對話中的職責、決策優先與熟悉度；未知時用白話摘要、不追問職稱。
  改變說明深度與順序，不改 facts、不確定性或限制。受眾不是 Grafana permission role。
- 保留 exact Preview、完整資料、防 leakage、train/CV-only selection、holdout、
  budget、opaque refs、receipt recovery、報告 evidence schema、text-only inspection、
  唯一 writer 與另行 publication 確認。變更 target/fields/split/operation/purpose/scope
  若超出已確認契約，需新版 Preview；不得將舊確認解釋為 blanket permission。
- 泛用 Python 不能繞過 structured ML validator 或自行宣稱 verified ML。新方法／
  split 不受支援時說明限制，不靜默換法。

## 驗證與限制

Base：`dd6d9ecd7b8b84592421aa3430aeacde2391313d`（dirty repository）。
Upstream pinned source：`8395ae10c3e38beae56329e4174a14a9a6d4c680`。
本次 installer stack：13 patches。原本 dirty/untracked source 不屬本次提交範圍；未 stage/commit/push。

主要重現命令（無 credentials／網路／服務需求；Go/source/cache 必須已在本機）：

```bash
python3 scripts/check-ask-o11y-autonomous-analyst.py
python3 scripts/check-ask-o11y-patch-stack.py
python3 scripts/configure-ask-o11y-workflow-tools.py --local-defaults --self-check \
  --out "$PWD/.scratch/analyst-evidence/settings-dry-run.json"
python3 scripts/check-no-fixed-analysis-flow.py
python3 scripts/check-data-understanding-skill.py
python3 scripts/check-ml-skill-advisory.py
bash -n scripts/build-install-ask-o11y.sh
```

觀察：

| 條件 | 觀察／證據 | 判定 |
| --- | --- | --- |
| 舊 prompt/Skill 能觸發回歸 | 隔離 clone 將三份內容還原至 overlay 前，保留新測試；兩 package 因舊指令與缺失責任而失敗；finally 還原 candidate | met（負例） |
| 新內容實際進入 default prompt／selected skills | PromptRegistry（Viewer/Editor/Admin）、availableAgentSkills、selectedSkillMessages 測試通過；custom override 保留 | met（組裝） |
| 設定 helper 不遺漏責任 | build_json_data + validate_payload + 真實 CLI dry-run 通過，不呼叫 --apply | met（組裝） |
| 最終 stack 可重建 | 13 patches 套用成功；新 checker 通過；既有 stack checker 的 Go/auth-refresh 檔案比對亦通過 | met |
| 既有 runtime regression | 隔離 candidate 執行 `go test ./pkg/agent ./pkg/plugin ./pkg/mcp -count=1` 全數通過 | met（回歸） |
| 自然語言可自主分析、角色呈現正確 | 未部署、未呼叫 live LLM／Grafana；文字契約檢查不能證明模型會遵循 | indeterminate |
| 獨立 review | team 預檢缺既有 skill，未啟動 child；main 做來源／護欄檢查，不冒稱獨立審查 | indeterminate |

完整資料與報告欄位約束未改 code，因此此批不宣稱重新驗證統計正確性或全平台安全。
LSP、Python compile、shell syntax、git diff whitespace 均另行檢查。原始本機 logs：
`.scratch/analyst-evidence/{baseline-negative,reconstructed-contracts,go-tests}.log`。
CLI 第一次用 relative `--out` 遇到既有 `relative_to(ROOT)` 錯誤；改用上述 absolute
path 後成功，沒有為此擴大修改 CLI。

## 啟用與後續 live 驗收

需另行核准 build/install/restart 與組織設定更新；不要直接使用本文件當部署授權。
保留並審閱既有 custom prompt／設定差異後才決定如何遷移；不能為更新 prompt
盲目用 fresh settings payload 覆蓋組織工具設定。未改設定時，新 Go default 對非空
custom prompt 不生效。

在新版本、授權資料與新會話使用以下輸入；不預先提供演算法，不把以下案例写成 runtime 分支：

| 自然語言輸入 | 觀察要求 |
| --- | --- |
| 最近不良率上升，幫我找值得先查的地方 | 提出合理的調查與驗證方法，不問用哪個模型；必要的資料／業務語義問題仍可問；執行前有 exact Preview |
| 我是廠長，這份資料告訴我該注意什麼？ | 先給決策意義與限制，不是只列統計量；無杜撰金額／因果 |
| 請把同一份結果改用製程工程師看得懂的方式說明 | 共用相同 evidence，增加可驗證條件／技術細節；不重跑 query/ML，不改數值／不確定性 |
| 換一份欄位與結構不同的資料，幫我理解問題 | 從真實 metadata 重擬策略，無固定欄位、chart/model 順序 |
| 資料不足但請預測下期表現 | 誠實揭露能力／資料不足，不偽造模型成功 |
| 只想了解資料，不要做 Dashboard | 回答問題即可，不強制寫入或出版 |
| 追加一個不在原契約中的分析 | 新 Preview／確認先於新 execution；不擴大舊授權 |

保存實際 prompt/selected-skill hashes、tool-call／execution receipts、數值來源與受眾
前後版本；檢查使用者是否仍被要求提供方法、方法是否合理、證據是否回答問題。
不得用「有產出報告」或 LLM 自評 PASS 代替這些觀察。

## 參考與回顧

設計借鏡（未安裝或複製外部程式碼）：

- <https://github.com/anthropics/knowledge-work-plugins/tree/main/data>
- <https://github.com/K-Dense-AI/scientific-agent-skills>
- <https://github.com/microsoft/data-formulator>

本批教訓：確認真正的 prompt 組裝與設定覆寫入口比單純加 Skill 更重要；patch repo
必須測最終 overlay，不能只測歷史新增檔；prompt regression 只能證明組裝及禁語，
模型分析品質仍需要 live evidence。後續先驗證上述案例，再按實際缺口調整 capability
重選或授權範圍，避免先新增另一個 orchestration framework。
