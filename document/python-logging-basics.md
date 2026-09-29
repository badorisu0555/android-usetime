# Python `logging`ライブラリの基本と、今回のLambdaでの使い方

## この記事について

このプロジェクトの各Lambda関数(`app/deliver_contents/main.py`など)の先頭には、必ず次のようなコードが書かれています。

```python
logger = getLogger(__name__)
handler = StreamHandler()
handler.setLevel(DEBUG)
formatter = Formatter('[%(levelname)s%(asctime)s%(message)s%(name)s]')
handler.setFormatter(formatter)
logger.setLevel(DEBUG)
logger.addHandler(handler)

logger.info('Creating childhood content is starting...')
```

`print()`で済みそうなところを、なぜこんなに何行も書いているのか。この記事では、Python標準の`logging`ライブラリの基本的な仕組みと、このコードが1行ずつ何をしているかを、初心者向けに整理します。

## そもそも`print()`ではなく`logging`を使うのはなぜか

`print()`は「画面に文字を出す」だけの機能ですが、`logging`には次のような`print()`には無い機能があります。

- **重要度(レベル)を分けられる**: 「デバッグ用の細かい情報」と「本当に困ったときのエラー」を区別できる。例えばエラーの全体像を把握したいだけなのに、デバッグ用の細かい情報まで入っていると読むときに困る。
- **出力先を後から変更・追加できる**: 画面だけでなく、ファイルやAWS CloudWatch Logsなど、複数の場所に同時に出力できる。またその設定変更が簡単にできる。
- **本番環境で出力量を調整できる**: 「本番の環境ではINFO以上のログだけ出す」「開発中はDEBUGのログまで全部出す」のように、コードを変えずにログに出力される量を制御できる。

AWS Lambdaのように「実行結果を直接目で見れない(CloudWatch Logsを見に行くしかない)」環境では、この「後から出力先やレベルを制御できる」という特性がとても重要になります。

## `logging`を構成する4つの登場人物

`logging`ライブラリを理解する上で、まず次の4つの役割を覚えると全体像がつかみやすくなります。

| 登場人物 | 役割 | たとえるなら |
| --- | --- | --- |
| **Logger** | `getLogger(名前)`という関数を呼び出して用意する。ログを受け取り、記録するかどうかを判断する窓口 | 「報告を受け付ける担当者」 |
| **Handler(配達係)** | ログの出力先(画面・ファイル・CloudWatchなど)を決める | 「報告書をどこに届けるかの配達員」 |
| **Formatter(整形係)** | ログの見た目(どんな形式の文字列にするか)を決める | 「報告書のフォーマット(様式)を決める人」 |
| **Level(重要度)** | ログの重要度。これより低い重要度のログは無視される | 「この重要度以上だけ報告して、というフィルター」 |

この4つが「Logger → Handler → Formatter」という順番で連携して、最終的に1行のログが出力される、という流れになっています。

```
ログを出す(logger.info(...))
  → Loggerが「このレベルなら扱ってよいか」を判定
  → Handlerに引き渡す
  → Handlerが「このレベルなら出力してよいか」を判定
  → Formatterで見た目を整形
  → 出力先(画面・ファイル・CloudWatchなど)に書き出す
```

## 冒頭のコードを1行ずつ解説

```python
logger = getLogger(__name__)
```
`getLogger(名前)`という**関数を呼び出して、Loggerオブジェクトを取得している**行です。引数に渡している`__name__`は、そのファイル(モジュール)の名前が自動的に入る特殊な変数で、これを名前として渡すことで「どのファイルから出たログか」を後から区別しやすくなります。

```python
handler = StreamHandler()
```
**Handler(配達係)を作る**行です。`StreamHandler`は「標準出力(コンソール画面)に出力する」タイプのHandlerです。AWS Lambdaでは、標準出力に出したものが自動的にCloudWatch Logsに送られる仕組みになっているため、Lambda上でログを残すには基本的にこの`StreamHandler`で十分です。

#### 参考: `StreamHandler`以外のHandler

Handlerは「ログをどこに出すか」を決める役割なので、**ログをファイルに出力するのか、コンソールにだけ表示させるのか、といった「どこに情報を出すのか」という設定はHandler側で行います**。`logging`には`StreamHandler`以外にもHandlerの種類があり、代表的なものに**`FileHandler`**があります。

```python
from logging import FileHandler

# ログを指定したファイルに書き込むHandler
file_handler = FileHandler('app.log', mode='a', encoding='utf-8')
```

| 項目 | 内容 |
| --- | --- |
| 役割 | ログを**指定したファイルに書き込む**Handler。実は`StreamHandler`を継承したクラスで、出力先を「コンソール」から「ファイル」に変えたものにあたる |
| 主な引数 | `filename`(書き込み先のファイルパス。必須) / `mode`(書き込みモード。省略時は追記の`'a'`) / `encoding`(文字コード) / `delay`(`True`にすると、実際に最初のログが出力されるまでファイルを開かない) |
| 使いどころ | ローカルPCやオンプレミスのサーバーなど、「ログをファイルとして残しておきたい」環境 |

ただし、**AWS Lambdaでは`FileHandler`はあまり使いません**。理由は次の2点です。

- Lambdaの実行環境で書き込みが許可されているのは`/tmp`ディレクトリのみで、しかも実行環境(コンテナ)が使い回されなければ**再実行のたびに消えてしまう**一時的な領域でしかない
- 標準出力に出すだけで自動的にCloudWatch Logsに送られるため(=`StreamHandler`で十分なため)、わざわざファイルに書き出す必要がない

つまり、このプロジェクトのコードが`StreamHandler`だけを使っているのは、「Lambda環境では、ファイルに書くよりCloudWatch Logsに送るほうが確実で扱いやすいから」という理由だと考えると理解しやすくなります。

ほかにも、時間に基づいて出力先のファイルをローテーションする`TimedRotatingFileHandler`や、一定のファイルサイズを超えると新しいログファイルを作成する`RotatingFileHandler`といったHandlerもあります(いずれも`logging.handlers`モジュールに含まれます)。

(`FileHandler`のより詳しい引数は[Python公式ドキュメント「logging.handlers」](https://docs.python.org/ja/3/library/logging.handlers.html#filehandler)を参照してください。)

```python
handler.setLevel(DEBUG)
```
**このHandlerが扱う最低レベルをDEBUGに設定する**行です。「DEBUG以上(=ほぼ全部)のログはこのHandlerに出力させる」という意味になります。

```python
formatter = Formatter('[%(levelname)s%(asctime)s%(message)s%(name)s]')
```
**Formatter(整形係)を作る**行です。ログ1行の「見た目のテンプレート」を、あらかじめ決まったプレースホルダー(`%(xxx)s`の形式)を組み合わせて作ります。このコードで使われているプレースホルダーが何を表すかは次の通りです。

| プレースホルダー | 意味 | 出力例 |
| --- | --- | --- |
| `%(levelname)s` | ログレベルの名前 | `INFO`, `ERROR` など |
| `%(asctime)s` | ログが出力された時刻 | `2024-01-15 10:30:00,123` |
| `%(message)s` | `logger.info(...)`などに渡した本文 | `Creating childhood content is starting...` |
| `%(name)s` | Loggerの名前(`getLogger(__name__)`で渡した`__name__`の値) | `main` |

(他にも`%(filename)s`や`%(lineno)d`など多数のプレースホルダーがあります。一覧は[Python公式ドキュメント「LogRecord属性」](https://docs.python.org/ja/3/library/logging.html#logrecord-attributes)を参照してください。)

このプロジェクトの書式`'[%(levelname)s%(asctime)s%(message)s%(name)s]'`をそのまま当てはめると、実際の出力は次のようになります。

```
[INFO2024-01-15 10:30:00,123Creating childhood content is starting...main]
```

**プレースホルダーの間にスペースや`,`などの区切り文字が入っていないため、どこからどこまでが何の情報か非常に読みにくい**点に注意してください。区切り文字を入れると、例えば次のように読みやすくなります。

```python
# 項目の間にスペースや記号を入れて読みやすくした例
formatter = Formatter('[%(levelname)s] %(asctime)s %(name)s: %(message)s')
```

```
[INFO] 2024-01-15 10:30:00,123 main: Creating childhood content is starting...
```

```python
handler.setFormatter(formatter)
```
**HandlerにFormatterを取り付ける**行です。これで「このHandlerから出るログは、このテンプレートの形式にする」という設定が完了します。

```python
logger.setLevel(DEBUG)
```
**Logger自身が扱う最低レベルをDEBUGに設定する**行です。ここが重要なポイントで、**LoggerとHandler、両方にレベル設定がある**ことに注意してください(詳しくは次の章で説明します)。

レベルは重要度の低い順に、`DEBUG < INFO < WARNING < ERROR < CRITICAL`という並びです。例えば`logger.setLevel(INFO)`にすると、`logger.debug(...)`で書いたログはLoggerの時点でブロックされ、Handlerまで届きません。

```python
logger.addHandler(handler)
```
**LoggerにHandlerを取り付ける**行です。これで「このLoggerに来たログは、このHandler経由で出力する」という配線が完成します。

```python
logger.info('Creating childhood content is starting...')
```
ここでようやく**実際にログを1行出力**しています。`.info(...)`は「INFOレベルでこのメッセージを記録して」という意味です。

## なぜLoggerとHandler、両方にレベル設定が必要なのか

初心者がつまずきやすいポイントです。ログは次の**2段階のフィルター**を通過して初めて出力されます。

1. `logger.setLevel(...)` ← 1段階目の関所(Logger側)
2. `handler.setLevel(...)` ← 2段階目の関所(Handler側)

どちらか一方でも「このログのレベルでは通さない」と判断されると、そのログは出力されません。**両方とも「これ以上の重要度なら通す」という設定なので、両方をDEBUGにしておくと、実質「全部通す」という意味になります**。

では、なぜわざわざ2段階に分けているのでしょうか。「両方DEBUGにしておけば全部通るだけなら、最初から1段階でよいのでは?」と思うかもしれません。この2段階フィルターが本当に活きるのは、**1つのLoggerに複数のHandlerをぶら下げて、Handlerごとに扱うレベルや出力先を変える**場合です。

例えば「デバッグ用の細かいログは通常のログ置き場に出しつつ、エラーだけは別の監視・アラート用の出力先にも流したい」というケースを考えます。

```python
logger = getLogger(__name__)
logger.setLevel(DEBUG)  # Logger側は広めに「DEBUG以上は扱う」としておく

# 1つ目のHandler: 通常運用ログ用。DEBUG以上を全部出す
debug_handler = StreamHandler()
debug_handler.setLevel(DEBUG)
logger.addHandler(debug_handler)

# 2つ目のHandler: アラート用。ERROR未満はこのHandler側でブロックされる
alert_handler = StreamHandler()
alert_handler.setLevel(ERROR)
logger.addHandler(alert_handler)
```

こうしておくと、

- `logger.debug(...)` / `logger.info(...)` は `debug_handler` だけを通過する(`alert_handler`側で止められる)
- `logger.error(...)` は両方のHandlerを通過する

という制御が、**呼び出し側(`logger.error(...)`などを書く場所)のコードを一切変えずに**実現できます。「どのログをどこに送るか」をHandler側の設定だけでコントロールできることが、LoggerとHandlerを分けている最大のメリットです。

現状このプロジェクトでは`StreamHandler`を1つだけ使い、Logger・Handlerとも同じ`DEBUG`に設定しているため、実質「全部同じ出力先(CloudWatch Logs)に流す」というシンプルな構成になっています。ただし仕組みとしては、将来「`[ALARM:...]`を含むエラーログだけを別のHandlerで拾って、別の通知チャネルに送る」といった拡張を、Logger呼び出し側のコード(`logger.error(...)`など)を変えずに追加できる、という点は覚えておくと良いポイントです。

## 今回のユースケースでの使い方

このプロジェクトでは、下記のような使い分けをしています。

| メソッド | 用途 | 例 |
| --- | --- | --- |
| `logger.debug(...)` | 開発中の細かい確認用ログ | `[DEBUG] Processing child: ...` |
| `logger.info(...)` | 「今どの処理をしているか」の進捗ログ | `Getting delivery content from DynamoDB...` |
| `logger.error(...)` | 失敗を記録するログ。CloudWatchアラームの検知対象にもなる | `[ALARM:DYNAMO_READ_FAILED] table=..., error=...` |

特に`logger.error`に`[ALARM:xxx]`という決まった文字列を含めているのは、**CloudWatch Logsのメトリクスフィルタでこの文字列を検索してアラームを発火させるため**です。「ログに何を書くか」は、単なるデバッグ用のメモではなく、監視の仕組みと直結している、という点がこのプロジェクトでの重要な使い方になっています。

また、AWS Lambda環境では標準出力がそのままCloudWatch Logsに送られるため、`StreamHandler`(画面に出すHandler)だけで、追加のライブラリや設定をしなくてもクラウド上でログが確認できる、という点もLambdaならではのポイントです。

## まとめ

- `logging`は「Logger」「Handler(配達係)」「Formatter(整形係)」「Level(重要度)」の4つの役割が連携して動く仕組み
- ログは「Logger→Handler」の2段階のレベルチェックを通過して初めて出力される
- AWS Lambdaでは`StreamHandler`で標準出力に出すだけで、自動的にCloudWatch Logsに記録される
- このプロジェクトでは、`logger.error`のログ本文が単なるメモではなく、CloudWatchアラームの検知条件そのものになっている