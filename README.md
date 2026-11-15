# jp-postal-code-watcher

日本郵便の郵便番号データ（住所データ）の更新を検出するウォッチャー（Python、標準ライブラリのみ）。

月次で全件スナップショットを取得し、前回のスナップショットと diff して「いつ・何が変わったか」を変更イベントとして記録します。公式の月次差分ファイル（utf_add / utf_del）は検出には使わず、検出結果の照合に使います。変更の適用先は差し替え可能なインターフェース（Applier）に切ってあり、具体的な適用先は未定のまま使えます。

## 使い方

手元で確認する：

```sh
python3 watch.py check \
  --snapshot data/current.csv.gz \
  --new https://www.post.japanpost.jp/service/search/zipcode/download/utf/zip/utf_ken_all.zip \
  --date 2026-11-03 \
  --events-json changes/events_2026-11.json \
  --events-csv changes/events_2026-11.csv
# added=6 removed=5
# HAS_CHANGES=1
```

- `--snapshot` が前回スナップショット、`--new` が新しい全件データ（zip の URL、zip ファイル、CSV のいずれか）
- `--official-add` / `--official-del` を渡すと公式差分と照合し、removed イベントに廃止理由を補完します
- `--update-snapshot` を付けると、変更があったときだけスナップショットを新しいデータで置き換えます
- `--apply NAME` で適用先を選べます（v1 は `none` のみ。適用先は `APPLIERS` に登録して追加します）

運用は GitHub Actions で月次実行します（`.github/workflows/watch.yml`）。変更があればイベントファイルとスナップショットを bot がコミットするので、リポジトリの履歴自体が変更履歴になります。

## 変更イベントの形式

`date`（検出日）/ `change_type`（`added` / `removed`）/ `jis_code`（全国地方公共団体コード5桁）/ `postal_code` / `prefecture_name` / `city_name` / `town_name` / `town_kana` / `reason_code` / `reason_label` / `record`（元レコード）

理由コードは公式の変更理由です：0=変更なし、1=市政・区政・町政・分区・政令指定都市施行、2=住居表示の実施、3=区画整理、4=郵便区調整等、5=訂正、6=廃止。

## テストと検証結果

```sh
python3 test_watch.py       # 単位テスト
python3 test_real_data.py   # 実ファイルでの統合テスト（data/ に実ファイルがある場合のみ）
```

検証の方法と結果は次のとおりです（2026-11-15 実施、2026年10月30日更新分に対して）。

- レコードの同一性は第1〜13列で取り、第14列（更新の表示）と第15列（変更理由）は同一性から外します。現行ファイル全124,527行でキーの重複がないことを確認しました。
- 検出方式の実証：10月分の全件から公式差分を逆適用して9月分を復元し、本ツールの diff を取ったところ、公式の utf_add（6件）/ utf_del（5件）と**完全一致**しました。スナップショット diff が公式差分を取りこぼさないことの確認です。
- 第1列の全国地方公共団体コードは、jp-local-gov-codes（ツール#4）の `code5` と同じ体系なので、#4 の生存期間データと突き合わせて「その市区町村コードは当時有効だったか」まで検証できます。

## 注記

- 監視対象は住所データ（utf_ken_all）のみです。事業所の個別郵便番号データは含みません。
- 「変更」は旧レコードの `removed` と新レコードの `added` の組として現れます（例：区画整理でフラグだけ変わる場合）。郵便番号だけをキーにするとこの組を見落とすため、同一性は第1〜13列で取っています。
- removed イベントの理由は、スナップショットだけでは分かりません。公式 del ファイルを渡した場合に限り、理由コード（通常は6=廃止）が補完されます。詳しい理由（区画整理など）は、対になる added イベント側の理由コードに出ます。
- イベントの `date` は検出日です。データ自体の更新日（日本郵便の公表日）とは数日ずれることがあります。
- 元データの出典は日本郵便「郵便番号データダウンロード」（https://www.post.japanpost.jp/service/search/zipcode/download/ ）です。郵便番号データについて日本郵便は著作権を主張しません（公式 readme 記載）。

## ライセンス

MIT
