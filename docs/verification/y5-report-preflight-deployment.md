# Y5 report preflight 修補部署與續測

使用者於2026-09-10明確要求「佈署後繼續測試」。主代理執行，沒有child、Goal變更或commit/push。

## 部署

相對上次Python runtime fingerprint，僅三檔變更：

- `sandbox-analysis-mcp/server.py`：安全且有界的host report source建立後，即使figure preflight拒絕仍保存來源與digest；無來源時不宣稱可repair。保留既有repair身分、digest、lineage與不重算限制。
- `ml_plotly_contract.py`與其Sandbox鏡像：單Cartesian numeric reference lines、boxpoints、typed 2D heatmap、colorbar/z bounds/reversescale及严格RGB→等值hex。仍拒絕NaN、任意shape path/URL與超出budget的資料。

使用既有依賴image做code-only build，未安裝套件。首次把裸image ID交給BuildKit FROM被當成repository名稱而失敗；確認本機原image ID後建立專用local tag，核對相同ID再build成功，沒有新增registry權限。失敗與成功log分開保存。

新image八個COPY檔案hash與checkout一致；在read-only、network-none容器驗證reference lines。`.env`只更新SANDBOX_IMAGE，保留90天retention與其他設定。只restart Sandbox及Bridge user units，沒有重啟Grafana、gateway或DB。五個authenticated MCP tools/list通過，部署前2,654個原artifact檔案逐hash保持不變。

證據目錄：`.scratch/y5-report-deploy-20260910/`。image-id.txt、base-image.txt、source.json、28檔source-snapshot、image-check.json、runtime.txt、service-readback.json及artifacts-before.json記錄精確狀態。

## 驗證與續測界線

Plotly正負回歸、public producer early-preflight拒絕→保存來源→零重算repair及既有lineage負向案例PASS；LSP三個runtime來源零errors。

僅對舊run_642c6c14aa82499abab2af8c58aa316f的JSON做read-only格式驗證，沒有執行其Python或改artifact：九圖中八圖現在可驗證，剩下一圖仍正確拒絕NaN。生成程式把IQR=0、無法估計的標準化差排序到前方並送圖，不能替換為0或放寬finite檢查。

沿用原session nytPyA9uBHhogJGjO-49y8wTfd7qhiEKpy0dr-4R610，以使用者本次授權請Ask O11y先inspect原程式，再建立新的修正版分析；不補造舊host report source、不改舊receipt、不重新上傳或查詢。保留原缺值核准，要求明列不可估計項並完整呈現分析流程。

續測run：p7DcAzKT2R0dsdLmVfwE6UCKqe1RbFA_lBPW3iWafd8，現已完成。啟動與事件證據`.scratch/y5-process-engineering/run-5-*`。

## 真流程與瀏覽器讀回

- 事件記錄只有一次revise_python_analysis，沒有重新query。新run_b1a9de873e164690bb86ec4ecbfc35f1計算成功、report accepted，保存host source digest並連回舊provenance。舊產物未改動。
- 實際輸入139、核准排除1、熱耗比較138日；煤源完整分配127、不完整11日。四個燃燒風門欄位unique_values=1、IQR=0，明列不可估計而非填零。上述數字讀自實際新產物，不執行擷取的Python。
- Ask O11y真prepare→inspect→compose→Grafana writer成功，UID `coal-heat-preview-b1a9` version 1。讀回22 panels，其中10 Plotly圖，涵蓋資料稽核、分佈、時間、國家支援/混煤、條件調整、相關性、低熱耗比較與工程限制。
- 本機現有Playwright及Chromium 143，1440×1000，真URL登入與捲動後10個Plotly DOM均完成SVG/layout，沒有page error；未下載browser或record認證資料。初次探針以aria-label去重，兩張不同圖恰好使用相同群組名稱，造成9/10假失敗；保留failed log，改查實際10個rendered DOM，沒有降低預期數量。證據browser-final.json與截圖。部分panel有內部捲動，這不是全面視覺/可及性驗收。
- Preview：<http://localhost:3000/d/coal-heat-preview-b1a9/a8859f3>

**仍為partial**：原plan沒有可機器評估的requirements，`REQUIREMENTS_NOT_RECORDED`仍顯示在Dashboard，不補造需求或宣稱完整分析驗收；亦未聲稱因果、核准設備設定、正式發佈、獨立review或Goal完成。最終receipt為`acceptance.json`；交使用者手動檢視。
