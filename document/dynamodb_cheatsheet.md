# DynamoDB操作チートシート(Python / boto3)

自分のプロジェクト(LINE配信Bot × Lambda構成)で実際に使ったDynamoDB操作パターンを、汎用的な形に置き換えてまとめたチートシートです。
「とりあえずこれをコピペして書き換えれば動く」を目指しています。

## 目次

- [0. 前提: resourceとclientの使い分け](#0-前提-resourceとclientの使い分け)
- [1. テーブル作成(IaC)](#1-テーブル作成iac)
  - [1-0. まず用語を整理する(パーティションキー・ソートキーとは)](#1-0-まず用語を整理するパーティションキーソートキーとは)
- [2. TTL(有効期限)の設定](#2-ttl有効期限の設定)
- [3. 書き込み: put_item](#3-書き込み-put_item)
- [4. 一括書き込み: batch_writer](#4-一括書き込み-batch_writer)
- [5. 単体取得: get_item](#5-単体取得-get_item)
- [6. 検索: query(GSI + KeyConditionExpression)](#6-検索-query-gsi--keyconditionexpression)
- [7. 更新: update_item(条件付き更新・アトミック加算)](#7-更新-update_item条件付き更新アトミック加算)
- [まとめ表](#まとめ表)
- [参考リンク](#参考リンク)

---

## 0. 前提: resourceとclientの使い分け

boto3には低レベルAPIの`client`と、高レベルAPIの`resource`の2種類がある。

```python
import boto3

# resource: Item操作(get_item/put_item/query等)をPythonの辞書感覚で書ける。通常はこちらを使う
dynamodb = boto3.resource("dynamodb", region_name="ap-northeast-1")
table = dynamodb.Table("my-table")

# client: create_table/update_time_to_live等、テーブル自体の管理operationはclientにしかない
client = boto3.client("dynamodb", region_name="ap-northeast-1")
```

- Lambda上で実行する場合、`region_name`は省略してもLambda実行リージョンが自動で使われる
- `boto3.resource`や`Table`オブジェクトの生成は、Lambdaのハンドラー関数の**外**(コールドスタート時に1回だけ)で行うのが定石。ハンドラー内で毎回作ると、リクエストのたびに無駄な初期化コストがかかる

```python
# Lambdaのファイルの一番外側(モジュールレベル)に書く
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table("my-table")

def lambda_handler(event, context):
    # ここでは table を使い回すだけ
    ...
```

---

## 1. テーブル作成(IaC)

コンソールを使わず、コードでテーブルを作るパターン。GSI(Global Secondary Index)を1つ持つ例。

### 1-0. まず用語を整理する(パーティションキー・ソートキーとは)

`AttributeDefinitions` / `KeySchema` / `GlobalSecondaryIndexes` に何を書けばいいか分からなくなる原因は、大体「パーティションキー」「ソートキー」が何なのかが曖昧なまま書き始めることにあります。先にここだけ押さえておきます。

| 用語 | コード上の表記 | 役割 |
|---|---|---|
| パーティションキー(Partition Key) | `KeyType: "HASH"` | アイテムがDynamoDB内部のどの物理パーティション(保管場所)に置かれるかを決める値。**検索するときは基本、この値の完全一致でしか絞り込めない** |
| ソートキー(Sort Key) | `KeyType: "RANGE"` | 同じパーティションキーを持つアイテムどうしを並べる・範囲で絞り込むための値(省略可)。`between`や`>`のような範囲検索ができるのはこのキーだけ |
| プライマリキー(Primary Key) | - | パーティションキー単体、または「パーティションキー+ソートキー」の組み合わせ。テーブル内でアイテムを一意に識別する |

例えば「配信ログテーブル」を例に、具体的なアイテムで考えるとこうなります。

```
delivery_id(パーティションキー)   user_id   category   delivered_at
------------------------------  --------  ---------  --------------------------
user-001#20260101#01             user-001  sleep      2026-01-01T09:00:00+09:00
user-001#20260102#01             user-001  food       2026-01-02T09:00:00+09:00
user-002#20260101#01             user-002  sleep      2026-01-01T09:00:00+09:00
```

このテーブルの本来のプライマリキーは`delivery_id`(パーティションキーのみ)なので、`get_item`で1件ピンポイントに取るのは得意です。しかし「`user-001`の直近7日分だけ欲しい」という検索は、`delivery_id`では絞り込めません。これが「GSI(別のパーティションキー・ソートキーの組み合わせを追加で持たせるインデックス)を作る理由」です。

これを踏まえて`create_table`の各パラメータを見ると、役割がはっきりします。

- **`AttributeDefinitions`**: これから「検索・並び替えのキーとして使う」属性の**型だけ**を宣言するリスト。DynamoDBはスキーマレス(`body`や`summary`のような属性は事前定義不要)なので、ここに書くのは`KeySchema`と`GlobalSecondaryIndexes`の`KeySchema`に登場する属性名だけでよい。逆に、キーとして使わない属性をここに書くとエラーになる
  - `AttributeType`は `"S"` = 文字列、`"N"` = 数値、`"B"` = バイナリ の3種類のみ
- **`KeySchema`**: テーブル本体のプライマリキーの定義。`"HASH"`(パーティションキー)を1つ必ず指定し、`"RANGE"`(ソートキー)は必要なら1つだけ追加できる
- **`GlobalSecondaryIndexes`**: 「本来のプライマリキーとは違う切り口で検索したい」ときに追加するインデックス。1つのテーブルに複数持てる。それぞれが独自の`IndexName`(`query`実行時に`IndexName=...`で指定する)、`KeySchema`(HASH/RANGEの組み合わせは本体と別に選べる)、`Projection`(そのインデックスに属性をどこまでコピーするか)を持つ

```python
import boto3

client = boto3.client("dynamodb")

client.create_table(
    TableName="my-table",
    AttributeDefinitions=[
        # KeySchemaとGlobalSecondaryIndexesで使う属性だけ定義すればよい
        # (それ以外の属性はスキーマレス=定義不要)
        {"AttributeName": "item_id", "AttributeType": "S"},
        {"AttributeName": "user_id", "AttributeType": "S"},
        {"AttributeName": "created_at", "AttributeType": "S"},
    ],
    KeySchema=[
        # HASH = パーティションキー(必須)。今回はitem_idの単一キーテーブル
        {"AttributeName": "item_id", "KeyType": "HASH"},
    ],
    GlobalSecondaryIndexes=[
        {
            "IndexName": "GSI1",
            "KeySchema": [
                # 「あるuser_idのデータをcreated_atの範囲で絞り込みたい」という
                # メインキー(item_id)とは異なるアクセスパターンのためにGSIを作る
                {"AttributeName": "user_id", "KeyType": "HASH"},
                {"AttributeName": "created_at", "KeyType": "RANGE"},
            ],
            "Projection": {
                # ALL: 全属性をGSIにコピー(読みやすいがストレージ・書き込みコスト増)
                # INCLUDE + NonKeyAttributesなら必要な属性だけに絞ってコスト削減できる
                "ProjectionType": "ALL",
            },
        }
    ],
    # PAY_PER_REQUEST: リクエスト数に応じた従量課金。
    # アクセス量が読めない個人開発・小規模サービスでは、キャパシティ設計が不要なこちらが楽
    BillingMode="PAY_PER_REQUEST",
)

# テーブル作成は非同期で走るので、ステータスがACTIVEになるまで待つ
waiter = client.get_waiter("table_exists")
waiter.wait(TableName="my-table")
```

`resource`ベースで書きたい場合は`table.wait_until_exists()`が使える。

```python
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table("my-table")
table.wait_until_exists()
```

---

## 2. TTL(有効期限)の設定

一定期間後に自動削除したい(配信ログ・キャッシュ等)場合、TTL属性を設定しておくと無料で自動削除してくれる。

```python
client.update_time_to_live(
    TableName="my-table",
    TimeToLiveSpecification={
        "Enabled": True,
        # このタイムスタンプ(Unix epoch秒、Number型)を過ぎたアイテムがDynamoDB側で自動削除される
        "AttributeName": "ttl",
    },
)
```

アイテム側では、削除したい日時をepoch秒で`ttl`属性に入れておくだけでよい。

```python
from datetime import datetime, timedelta, timezone

now = datetime.now(timezone.utc)
item = {
    "item_id": "abc123",
    # 7日後に自動削除されるようにしておく
    "ttl": int((now + timedelta(days=7)).timestamp()),
}
```

※ TTLによる削除は「だいたい期限切れの48時間以内」に実行される仕様で、厳密なタイミングでは消えない点に注意。

---

## 3. 書き込み: put_item

1件だけ書き込む、最も基本的な操作。

```python
table.put_item(
    Item={
        "item_id": "abc123",
        "user_id": "user-001",
        "created_at": "2026-09-16T10:00:00+09:00",
        "body": "サンプルテキスト",
    }
)
```

- 既に同じキーのアイテムが存在する場合、**デフォルトでは黙って上書き**される
- 「存在しない場合だけ作りたい(重複作成防止)」場合は`ConditionExpression`を使う

```python
from botocore.exceptions import ClientError

try:
    table.put_item(
        Item={"item_id": "abc123", "body": "サンプル"},
        # item_idがまだ存在しない場合のみ書き込みを許可する
        ConditionExpression="attribute_not_exists(item_id)",
    )
except ClientError as e:
    if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
        # 既に存在した = 想定内の競合なので、例外として扱わずログだけ残す運用が多い
        print("既に存在するためスキップ")
```

---

## 4. 一括書き込み: batch_writer

大量データ(バッチ処理でのニュース記事投入等)を書き込むときに使う。DynamoDBの`BatchWriteItem`は1リクエスト最大25件までという制限があるが、`batch_writer`を使うとその分割・リトライを自動でやってくれる。

```python
import numpy as np  # pandas由来のNaNを除外する例のため

def batch_write(items: list[dict], table_name: str):
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)

    with table.batch_writer() as batch:
        for item in items:
            # DynamoDBはNaNを受け付けないため、pandas.DataFrame由来のデータを
            # そのまま渡す場合はNaN値のキーを事前に除外しておく必要がある
            clean_item = {k: v for k, v in item.items() if v is not np.nan}
            batch.put_item(Item=clean_item)

    print(f"{len(items)}件を{table_name}に書き込み完了")
```

- `batch_writer()`は`with`ブロックを抜けるタイミングでバッファに溜まったリクエストをまとめて送信する
- 内部で自動的にリトライ・バッファリングしてくれるので、25件ごとに手動で分割する必要はない
- `delete_item`相当も`batch.delete_item(Key=...)`として使える

---

## 5. 単体取得: get_item

プライマリキーが分かっている1件を取得する、最もシンプルな読み取り。

```python
from botocore.exceptions import ClientError

try:
    response = table.get_item(Key={"item_id": "abc123"})
except ClientError as e:
    print(f"読み取り失敗: {e}")
    raise

# 該当アイテムが存在しない場合、response に "Item" キー自体が無いので get() で受ける
item = response.get("Item")
if item is None:
    print("該当データなし")
```

`get_item`は必ず「テーブルのプライマリキー」を指定する必要がある。プライマリキー以外の条件で絞り込みたい場合は次項の`query`か`scan`を使う。

---

## 6. 検索: query(GSI + KeyConditionExpression)

「あるuser_idについて、直近N日分だけ取得したい」のように、パーティションキー+範囲条件で複数件を絞り込む場合は`query`を使う。

```python
import time
from boto3.dynamodb.conditions import Key

def query_recent_items(user_id: str, days: int = 7, table_name: str = "my-table"):
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)

    now = int(time.time())
    start = now - days * 24 * 60 * 60

    response = table.query(
        # 1章で作ったGSI1(user_id + created_at)を指定
        IndexName="GSI1",
        KeyConditionExpression=(
            Key("user_id").eq(user_id)
            & Key("created_at").between(start, now)
        ),
    )
    return response.get("Items", [])
```

- `Key(...).eq()` / `.between()` / `.gt()` / `.lt()` などは`boto3.dynamodb.conditions.Key`が提供するヘルパー。文字列で`KeyConditionExpression`を手書きするより安全で読みやすい
- **`query`はパーティションキーの指定が必須**。パーティションキーなしで全件条件検索したい場合は`scan`を使うが、`scan`はテーブル(またはGSI)を全件読み込んでからフィルタするため、件数が多いテーブルではコスト・レイテンシの両方で不利。基本は`query`で済む設計(アクセスパターンに合わせたGSI設計)を優先する

```python
# 参考: どうしてもキー以外の条件で絞り込みたい場合のscan(非推奨・小規模データ向け)
from boto3.dynamodb.conditions import Attr

response = table.scan(
    FilterExpression=Attr("status").eq("published")
)
items = response.get("Items", [])
```

---

## 7. 更新: update_item(条件付き更新・アトミック加算)

### 7-1. 条件付き更新(二重処理防止)

「まだ評価されていない場合のみ更新したい」のような、Webhookの再送・二重処理対策で使うパターン。

```python
from botocore.exceptions import ClientError

try:
    response = table.update_item(
        Key={"item_id": "abc123"},
        UpdateExpression="SET score = :score, rated = :true",
        # attribute_not_exists(rated): まだratedが設定されていない(=初回)場合のみ許可
        # OR rated = :false: 将来falseを明示的に入れる運用に変えても対応できるようにしておく
        ConditionExpression="attribute_not_exists(rated) OR rated = :false",
        ExpressionAttributeValues={
            ":score": 5,
            ":true": True,
            ":false": False,
        },
        # ALL_NEW: 更新後の全属性を返す(更新後の値を使って後続処理をしたい場合に指定)
        ReturnValues="ALL_NEW",
    )
except ClientError as e:
    if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
        # 条件を満たさなかった = 既に処理済み。エラーではなく正常なスキップとして扱う
        print("既に評価済みのためスキップ")
```

- `ConditionExpression`は「更新して良い条件」。満たさない場合は`ClientError`(`ConditionalCheckFailedException`)が発生し、更新は行われない
- Lambda + Webhookの構成では、同じイベントが再送されてくることがあるため、この「条件を満たさなければ静かにスキップする」パターンは頻出する

### 7-2. アトミックな加算(カウンター・合計値の更新)

「取得→加算→書き込み」を1回のリクエストで安全に行いたい場合は`ADD`アクションを使う。

```python
table.update_item(
    Key={"user_id": "user-001"},
    # ADDはDynamoDB側でアトミックに加算してくれるので、
    # 同時に複数のリクエストが来ても値が上書きされず正しく合算される
    UpdateExpression="ADD score_sum :score, score_count :one",
    ExpressionAttributeValues={":score": 5, ":one": 1},
)
```

もし一度`get_item`で現在値を取得し、Python側で足し算してから`put_item`で書き戻す実装にすると、複数リクエストが同時に来たときに片方の更新が失われる(Lost Update)リスクがある。カウンターや合計値の更新は`ADD`を使うのが安全。

---

## まとめ表

| やりたいこと | 使うメソッド | ポイント |
|---|---|---|
| テーブルを作る | `client.create_table()` | GSIはAttributeDefinitionsに使う属性のみ定義 |
| 有効期限を設定する | `client.update_time_to_live()` | アイテム側はepoch秒(Number型)で保持 |
| 1件書き込む | `table.put_item()` | デフォルトは上書き。重複防止は`ConditionExpression` |
| 大量に書き込む | `table.batch_writer()` | 25件制限の分割・リトライを自動化 |
| キー指定で1件取得 | `table.get_item()` | プライマリキーの完全一致のみ |
| 範囲・複数件を取得 | `table.query()` | パーティションキー必須。GSIで検索パターンを増やせる |
| キー以外で絞り込む | `table.scan()` | 全件走査になるので基本は`query`で済む設計を優先 |
| 値を更新する | `table.update_item()` | `ConditionExpression`で二重更新防止、`ADD`でアトミック加算 |

---

## 参考リンク

> ⚠️ この記事を書いたセッションのネットワーク制限により、以下のリンクは実際に開いて内容を確認できていません。投稿前にリンク切れがないか確認してください。

- [boto3 Table.put_item](https://docs.aws.amazon.com/boto3/latest/reference/services/dynamodb/table/put_item.html) — put_itemのオプション一覧(ConditionExpression含む)
- [boto3 Table.get_item](https://docs.aws.amazon.com/boto3/latest/reference/services/dynamodb/table/get_item.html) — get_itemのオプション一覧
- [boto3 Table.update_item](https://docs.aws.amazon.com/boto3/latest/reference/services/dynamodb/table/update_item.html) — update_itemのオプション一覧
- [boto3 Table.query](https://docs.aws.amazon.com/boto3/latest/reference/services/dynamodb/table/query.html) — queryのオプション一覧
- [boto3 Table.batch_writer](https://docs.aws.amazon.com/boto3/latest/reference/services/dynamodb/table/batch_writer.html) — batch_writerの挙動
- [boto3 DynamoDB conditions (Key/Attr)](https://docs.aws.amazon.com/boto3/latest/reference/customizations/dynamodb.html) — KeyConditionExpression/FilterExpressionを書くためのヘルパー
- [AWS公式: Update Expressions](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Expressions.UpdateExpressions.html) — SET/ADD/REMOVE/DELETEアクションの仕様
- [AWS公式: Condition Expressions](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Expressions.ConditionExpressions.html) — ConditionExpressionの書き方
- [AWS公式: Global Secondary Indexes](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/GSI.html) — GSIの設計指針
- [AWS公式: Time To Live (TTL)](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/TTL.html) — TTLの削除タイミングの仕様
- [AWS公式: On-demand capacity mode](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/on-demand-capacity-mode.html) — PAY_PER_REQUESTの課金モデル
