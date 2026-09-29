## この記事について

Lambdaで動くバッチ処理に対して、「特定の失敗パターンだけをピンポイントでメール通知したい」というCloudWatchアラームを設計したときの型をまとめます。次に別プロジェクトで同じことをやるときに、そのまま流用できることを目指しています。

「失敗をどう検知してメールに届けるか」は、次の4つのAWSリソース設定の積み重ねで成立しています。

1. ALARMログ記録: ログ側で`[ALARM:xxx]`タグを仕込む
2. カスタムメトリクス設定: ログの出現回数を数値(メトリクス)に変換する
3. アラーム設定: メトリクスの値を閾値判定する
4. SNSトピック: 判定結果をメールに変換して届ける

## 全体設計

```
①[ALARM:xxx]ログ → ②メトリクスフィルタ(カスタムメトリクス設定) → ③アラーム(閾値判定) → ④SNSトピック → メール
```

- ①検知したい失敗が起きた箇所で、決まった文字列のタグを付けてログを出力する
- ②そのタグを含むログが出力された回数を、CloudWatchのカスタムメトリクスとして数値化する
- ③数値化されたメトリクスが閾値を超えたら、「アラーム状態」と判定する
- ④アラーム状態になったことを、SNS経由でメールとして送る

Lambda内で出力した1行のログが、人間の元にメールとして届くまでの流れです。以下、①〜④それぞれについて何を設定していて、なぜその設定が必要かを説明します。

## 1. ALARMログ記録

**ルール**: 「アラームを鳴らしたい失敗」が起きた箇所で、決まったプレフィックスを付けてログ出力する。これが後述のメトリクスフィルタが探す「目印」になる。

```python
logger.error(f"[ALARM:<検知したい事象の名前>] <調査に必要な情報>")
```

### 実例(このリポジトリでの適用)

`app/deliver_contents/main.py`でLINE配信APIの呼び出しに失敗した箇所の実装です。

```python
# app/deliver_contents/main.py

if response.status_code == 200:
    logger.info("Flex Messageが正常に送信されました")
else:
    # ALARM: プレフィックスはCloudWatch Logsメトリクスフィルタで拾うための目印(他の失敗と区別するため)
    logger.error(f"[ALARM:LINE_DELIVERY_FAILED] status={response.status_code}, body={response.text}")
    # ここで例外を送出しないと、LINE配信に失敗してもLambdaは正常終了(exit code 0)扱いになり、
    # CloudWatchアラームがLambdaの Errors メトリクスで検知できなくなるため、あえて呼び出し元に伝播させる
    raise RuntimeError(f"LINE API Error: status={response.status_code}, body={response.text}")
```

- `<検知したい事象の名前>` に当たる部分が `LINE_DELIVERY_FAILED`、`<調査に必要な情報>` に当たる部分が `status=...` `body=...`(`key=value`形式で原因調査用の情報を添えている)です

### 命名の指針

- `<検知したい事象の名前>` は英大文字+アンダースコア(例: `DYNAMO_READ_FAILED`)で統一する
- 名前は「何のシステムの・何の処理が・どう失敗したか」が分かるように付ける
- 同じログの中に、原因調査に使う変数(テーブル名、ステータスコード、例外内容など)を`key=value`形式で添える

### なぜこの形式でログを保存する必要があるのか

- 固定文字列のプレフィックスを先に決める(`[ALARM:xxx]`)。エラーメッセージの自然文だけで判定しようとすると、文言修正のたびにアラームが壊れる
- 1つの失敗パターン = 1つのタグ = 1つのメトリクス、を徹底する。複数の失敗を1つのタグにまとめると、後で「どのエラーだったか」の切り分けができなくなる
- タグは実装より先に「何を検知したいか」から逆算して決める(先にログを書いてから後付けでタグを考えない)

この3点を守っておくと、②のメトリクスフィルタが「ログ文字列の完全一致」だけで安定して動くようになります。

## 2. カスタムメトリクス設定

`AWS::Logs::MetricFilter`で、指定したロググループに`[ALARM:xxx]`という文字列を含むログが出力されるたびに、独自のカスタムメトリクスの値を+1する設定です。CloudWatchアラームは「メトリクス(数値の時系列データ)」にしか閾値判定を掛けられないため、①で仕込んだログの出現をここで数値に変換します。

```json
"XxxFailedMetricFilter": {
  "Type": "AWS::Logs::MetricFilter",
  "Properties": {
    "LogGroupName": { "Ref": "対象のLogGroup論理ID" },
    "FilterPattern": "\"[ALARM:XXX_FAILED]\"",
    "MetricTransformations": [{
      "MetricValue": "1",
      "DefaultValue": 0,
      "MetricNamespace": "任意のNamespace",
      "MetricName": "XxxFailed"
    }]
  }
}
```

- `FilterPattern`: ①のログ文字列と完全に対応させる部分。ここが一致しないとカウントされない
- `MetricTransformations`: 一致したログ1件につき、`MetricName`で指定したカスタムメトリクスの値を`MetricValue`(=1)だけ増やす、という変換ルール

## 3. アラーム設定

`AWS::CloudWatch::Alarm`で、②のカスタムメトリクスを一定期間(`Period`)ごとに集計(`Statistic`)し、閾値(`Threshold`)を超えたら「アラーム状態」に遷移させます。「どのくらい増えたら異常とみなすか」の判定基準をここで数値として決め、`AlarmActions`で条件を満たしたときのアクション(④のSNSへの通知)を指定します。

```json
"XxxFailedAlarm": {
  "Type": "AWS::CloudWatch::Alarm",
  "DependsOn": ["XxxFailedMetricFilter"],
  "Properties": {
    "AlarmName": "わかりやすいアラーム名",
    "Namespace": "任意のNamespace",
    "MetricName": "XxxFailed",
    "Statistic": "Sum",
    "Period": 3600,
    "EvaluationPeriods": 1,
    "Threshold": 1,
    "ComparisonOperator": "GreaterThanOrEqualToThreshold",
    "TreatMissingData": "notBreaching",
    "AlarmActions": [{ "Ref": "SNSトピック論理ID" }]
  }
}
```

- `Period`/`Statistic`/`EvaluationPeriods`: 「何秒間隔で」「どう集計して」「何回分見て」判定するかを決める部分。例では3600秒(1時間)ごとの合計値を1回分だけ見ている
- `Threshold`/`ComparisonOperator`: 「1以上になったら」異常とみなす、という条件
- `AlarmActions`: 条件を満たした(アラーム状態になった)ときに、実際に何を呼び出すか。ここに④のSNSトピックを指定する

## 4. SNSトピック

SNS(Simple Notification Service)は、届いたメッセージをメール・SMS・Lambda・他システムのAPIなど複数の宛先に配信できる、AWSの通知サービスです。「メッセージの送り先(トピック)」と「そのトピックを購読する宛先(サブスクリプション)」を分けて管理でき、CloudWatchアラームを含む多くのAWSサービスが、通知の飛ばし先としてこのSNSを利用します。

`AWS::SNS::Topic`と`AWS::SNS::Subscription`で、③のアラームが`AlarmActions`から通知を送る先のトピックと、そのトピックにメールアドレスを購読(Subscribe)させる設定です。CloudWatchアラーム自体はメールを直接送信できないため、通知を実際の配送先(ここではメール)に変換する仲介役としてSNSを挟みます。

```json
"AlertTopic": { "Type": "AWS::SNS::Topic", "Properties": { "TopicName": "..." } },
"AlertSubscription": {
  "Type": "AWS::SNS::Subscription",
  "Properties": { "TopicArn": { "Ref": "AlertTopic" }, "Protocol": "email", "Endpoint": { "Ref": "AlertEmail" } }
}
```

- `AlertTopic`: 通知の送り先となるSNSトピック本体
- `AlertSubscription`: そのトピックに届いたメッセージを、`Protocol: email`で指定したメールアドレス(`Endpoint`)に転送する購読設定

## まとめ

①ログに`[ALARM:xxx]`タグを仕込む → ②メトリクスフィルタで数値化する → ③アラームで閾値判定する → ④SNSでメールに変換して届ける、という4ステップは常にセットです。「新しい失敗パターンを検知したい」となったときは、この4ステップを同じ手順で機械的に追加していけます。