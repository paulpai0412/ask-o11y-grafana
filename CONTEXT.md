# Adaptive Ask O11y analysis context

本詞彙表區分決策問題、授權、分析證據與交付狀態。圖表存在、報告格式有效及預覽已保存，不等於決策問題已得到回答。

## Language

**Decision question（決策問題）**:
使用者希望理解或支持的業務決策問題，不要求使用者指定演算法或分析程序。
_Avoid_: Algorithm request, analysis procedure

**Analysis strategy（分析策略）**:
分析者根據問題、授權範圍與中間證據選擇及修訂的方法，不是預先固定的執行序列。
_Avoid_: Fixed workflow, mandatory model sequence

**Confirmed analysis contract（已確認分析契約）**:
使用者核准的特定版本分析提案，界定問題、資料及操作範圍；不是任意分析或重算的概括授權。
_Avoid_: Unrestricted autonomy, general consent

**Delivery requirements（交付要求）**:
已確認提案承諾回答的問題、比較與解釋，以及所需證據；不同問題可以有不同要求，不等同固定方法或圖表清單。
_Avoid_: Fixed DAG, required model sequence, panel count

**Bounded compute scope（有界運算授權）**:
在同一授權資料與約定預算內執行特定可驗證運算的許可，不涵蓋變更分析契約或任意程式。
_Avoid_: Blanket approval, permission to run arbitrary code

**Audience context（受眾情境）**:
使用者的職責、決策優先順序與技術熟悉度，影響解釋的深度與順序，但不改變事實或存取權限。
_Avoid_: Access role, permission grant

**Data profile（資料概況）**:
描述資料結構、完整性、分布與探索性關係的證據；只有當交付要求本來就是資料概況時，才可能構成完整交付。
_Avoid_: Decision answer, key-driver analysis by default

**Analysis finding（分析發現）**:
有證據支持且與決策問題相關的觀察或估計，不自動構成因果結論或獲准的操作介入。
_Avoid_: Proven root cause, guaranteed improvement

**Evidence coverage（證據覆蓋）**:
保留證據與交付要求的對應程度；未評估、部分覆蓋及已覆蓋彼此不同，證據存在也不自動證明方法有效。
_Avoid_: Artifact count, scientific proof, not-assessed-as-pass

**Report synthesis（報告解釋）**:
分析者以可追溯證據說明本題發現、關係、意義、限制與下一步，而非僅列出圖表名稱或通用免責文字。
_Avoid_: Chart assembly, boilerplate narrative

**Partial delivery（部分交付）**:
保留原始決策問題、提供已有支持的發現並明示尚未回答要求的有限交付，不是默默改題後的完整交付。
_Avoid_: Silent fallback, completed analysis

**Analysis delivery completion（分析交付完成）**:
已確認交付要求得到逐項核對，清楚說明已回答項目及證據能支持的邊界；不要求結論顯著、模型勝過基準或必然能改善操作。
_Avoid_: Chat turn completed, compute success, dashboard saved

**Grafana Preview（Grafana 預覽）**:
供使用者審閱且尚未正式發布的可視化交付物，可能呈現完整或部分分析交付；預覽存在本身不證明分析或視覺驗收完成。
_Avoid_: Published dashboard, analytical completion

**Report artifact repair（報告產物修復）**:
針對已保留結果的表示格式或證據匯出進行修復，不改變資料值、群組、分析方法或授權範圍；與重新執行分析不同。
_Avoid_: Blind retry, automatic recomputation
