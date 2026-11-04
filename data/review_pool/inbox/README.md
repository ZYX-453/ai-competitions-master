# 评论导入箱

把平台后台导出的评论文件（CSV / XLSX）放进这个目录，
然后运行 `python agent/backend/tools/collectors.py --scan`，
文件就会被解析、去重、并入语料池。

需要的列（列名可以是中文，会自动映射）：

| 必需 | 说明 | 常见叫法 |
|---|---|---|
| 是 | 评论文本 | 评价内容 / 评论内容 / content / review |
| 是 | 星级 | 评分 / 星级 / rating / score |
| 否 | 时间 | 评价时间 / 下单时间 / date |
| 否 | 类目 | 商品分类 / 类目 / category |
| 否 | 商品 ID | 商品编号 / sku / item_id |

导入后会写入 `imported` 来源，并在 `_manifest.json` 里留一条记录。
原始文件会被移到 `inbox/done/`，不会重复导入。
