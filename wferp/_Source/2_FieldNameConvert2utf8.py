import json
from typing import Any

# -- 轉換 iso-8859-1 to big5
def Convert_iso8859_to_big5(string):
    s = str(string)
    s = s.encode('iso-8859-1').decode('csbig5')
    return s

# -- 讀入 TableStructure.json
_TableStructure: list[dict[str, Any]] = []
try:
    with open('TableStructure.json', 'r', encoding="utf-8") as f:
        loaded = json.load(f)
    if isinstance(loaded, list) and all(isinstance(item, dict) for item in loaded):
        _TableStructure = loaded
except (OSError, json.JSONDecodeError) as e:
    print(e)

# -- 讀入 language.json
_LANG_JSON: list[dict[str, Any]] = []
try:
    with open('language.json', 'r', encoding="utf-8") as f:
        loaded = json.load(f)
    if isinstance(loaded, list) and all(isinstance(item, dict) for item in loaded):
        _LANG_JSON = loaded
except (OSError, json.JSONDecodeError) as e:
    print(e)
# -- 依據  中文(FieldName)，查詢其 越南文 (language.json from TABLE LANGUAGE)
def Get_NameVietnam(chinese_string):
    s = str(chinese_string).strip()

    for item in _LANG_JSON:
        if item.get('CHT') == s:
            if item.get('VIET') is not None:
                return item.get('VIET')
    return s

# -- 因爲這些 Table Name 的 欄位名稱和 Description 都是亂碼
_i_TableID = 'ADMXA,CMSXA,INVXA,PURXA,PURXB,PURXC,MTMXA,MTMXB,MTMXC,MTMXD,MOCXA,MOCXB,MOCXC,VPAXA,VPAXB,VPAXC,VPAXD'

i=0
for item in _TableStructure:
    _TableID = str(item['TableID']).strip()
    # -- 如果 TableID 在 _i_TableID 中，才處理
    for y in _i_TableID.split(','):
        if _TableID == y:
            _FieldName = str(item['FieldName']).strip()
            _Description = str(item['Description']).strip()
            _FieldName_utf8 = Convert_iso8859_to_big5(_FieldName).strip()
            _Description_utf8 = Convert_iso8859_to_big5(_Description).strip()

            # -- 如果 Description 等於 FieldName，Description = ''
            if _Description_utf8 == _FieldName_utf8:
                _Description_utf8 = ''

            _TableStructure[i]['FieldName'] = _FieldName_utf8
            _TableStructure[i]['Description'] = _Description_utf8
            _TableStructure[i]['NameVietnam'] = Get_NameVietnam(_FieldName_utf8)
            
            # -- 如果找到的 NameVietnam 等於 FieldName (都是中文)，NameVietnam = ''
            if _TableStructure[i]['NameVietnam'] == _TableStructure[i]['FieldName']:
                _TableStructure[i]['NameVietnam'] = ''

            break

    i = i + 1

# -- 寫入 TableStructure.json
try:
    _jsonData = json.dumps(_TableStructure, indent=2, ensure_ascii=False)
    with open('TableStructure.json', 'w', encoding="utf-8") as fs:
        fs.write(_jsonData)
except (OSError, TypeError, ValueError) as e:
    print(e)

