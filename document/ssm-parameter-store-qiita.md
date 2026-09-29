## AWSのSSMパラメータストアに.envの値を登録して、コードから呼び出すまで

APIキーやDBのパスワードといった秘密情報を、`.env`ファイルに書いてそのままGitにコミットしてしまった…という経験がある人は多いと思います。`.gitignore`に入れておけば防げるとはいえ、Lambdaやサーバーにデプロイするたびに手動でファイルをコピーするのも面倒ですし、うっかり公開リポジトリにpushしてしまうリスクも消えません。

その解決策としてよく使われるのが、AWSの**Systems Manager パラメータストア(以下SSM)**です。この記事では、

1. `.env`ファイルにパラメータをまとめる
2. それをSSMに登録するスクリプトを書く
3. アプリ側のコードでSSMから値を読み出す

という3ステップを、実際に動くコードを見ながら説明します。対象読者はboto3にまだ慣れていない人、SSMという名前は聞いたことがあるけど自分で書いたことはない人です。

### そもそもSSMパラメータストアって何なのか

一言でいうと、AWSが提供している「キーと値のペアを保存できる場所」です。環境変数を自分のPCの外に置いておいて、必要なときにコードから取りに行くようなイメージです。

似たようなサービスにSecrets Managerもありますが、パラメータストアは無料枠が広く、個人開発や小規模なプロジェクトであれば追加コストがほぼかからないという理由でよく使われます。詳しい違いは公式ドキュメントにまとまっています。

参考: [AWS Systems Manager Parameter Store(AWS公式ドキュメント)](https://docs.aws.amazon.com/ja_jp/systems-manager/latest/userguide/systems-manager-parameter-store.html)

パラメータには`String`(平文)、`StringList`、`SecureString`(KMSで暗号化)の3種類があります。APIキーやパスワードのような秘密情報は`SecureString`で保存するのが基本です。

### 全体の流れ

やることは大きく分けて2つだけです。

- **登録するスクリプト**: `.env`から値を読んで、SSMに`put_parameter`する
- **呼び出すコード**: アプリの起動時に、SSMから`get_parameter`で値を取ってくる

「登録は一度きり(値が変わったときだけ)」「呼び出しはアプリが動くたびに毎回」という点が違うので、それぞれ別のコードとして分けて考えると理解しやすいです。

### 1. .envファイルにパラメータをまとめる

まず、ローカルの`.env`ファイルに、SSMへ登録したい値を普通に書いておきます。

```env
API_KEY=xxxxxxxxxxxxxxxx
DB_PASSWORD=xxxxxxxxxxxxxxxx
LINE_CHANNEL_ACCESS_TOKEN=xxxxxxxxxxxxxxxx
```

ここでのポイントは、この`.env`は「SSMに登録するための一時的な作業ファイル」という位置づけにしていることです。デプロイ先で直接この`.env`を読むわけではなく、あくまでSSMへ値を送るための踏み台として使います。なので`.gitignore`には必ず入れておきます。

### 2. 登録スクリプトを書く

`.env`の値をSSMに登録するスクリプトです。ここでは`create_ssm_key.py`という名前にしています。

```python
import os
import boto3
from dotenv import load_dotenv

# .envファイルの内容を環境変数として読み込む
# これをやらないとos.environ.get()で値が取れない
load_dotenv()

ssm = boto3.client("ssm", region_name="ap-northeast-1")

# key: SSM上でのパラメータ名(スラッシュで階層を作れる)
# value: .env側でのキー名
# 両方を辞書で対応させておくと、ループ1回で全部登録できる
PARAMETERS = {
    "/myapp/API_KEY": "API_KEY",
    "/myapp/DB_PASSWORD": "DB_PASSWORD",
    "/myapp/LINE_CHANNEL_ACCESS_TOKEN": "LINE_CHANNEL_ACCESS_TOKEN",
}

def register_parameters():
    for ssm_name, env_key in PARAMETERS.items():
        value = os.environ.get(env_key)
        if not value:
            # .envに書き忘れているだけで処理自体を止めたくないので、
            # ここではエラーにせず警告を出して次のパラメータに進む
            print(f"警告: {env_key}が見つかりません。")
            continue

        ssm.put_parameter(
            Name=ssm_name,
            Value=value,
            Type="SecureString",   # 暗号化して保存する。平文で見えるStringは使わない
            Overwrite=True,        # 既に同じ名前のパラメータがあっても上書きする
        )
        print(f"{ssm_name}をSSMパラメータストアに登録しました。")

if __name__ == "__main__":
    register_parameters()
```

このコードで押さえておきたいのは3か所です。

**`region_name`を明示していること**
SSMのパラメータはリージョンごとに保存されます。あとで読み込む側と登録する側でリージョンが違うと「そんなパラメータは存在しない」というエラーになるので、両方で同じリージョンを指定しておくと事故を防げます。

**`Type="SecureString"`にしていること**
`String`だと暗号化されずそのまま保存されるので、マネジメントコンソールを見れば誰でも値が読めてしまいます。秘密情報を保存する以上、ここは`SecureString`が前提になります。

**`Overwrite=True`にしていること**
これを付けないと、2回目以降の実行で「既に存在するパラメータです」というエラーで落ちます。トークンやパスワードをローテーションしたときに何度も実行し直すスクリプトなので、上書きを許可しておくのが自然です。

`put_parameter`に渡せるパラメータの詳細は公式ドキュメントを見ると分かりやすいです。

参考: [put_parameter(boto3公式ドキュメント)](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/ssm/client/put_parameter.html)

`load_dotenv()`の挙動(どこの`.env`を読むか、既存の環境変数を上書きするかどうかなど)については、python-dotenvのリポジトリのREADMEに一通り載っています。

参考: [python-dotenv(GitHub)](https://github.com/theskumar/python-dotenv)

このスクリプトを実行すれば、`.env`の内容がSSM側にコピーされます。

```bash
python create_ssm_key.py
```

### 3. アプリ側でSSMから値を読み込む

登録が終わったら、今度は本体のアプリケーション側でその値を取り出す必要があります。こちらは共通の関数を1つ作っておくのが定番です。

```python
import boto3

def get_ssm_parameter(parameter_name):
    ssm = boto3.client("ssm")
    # SecureStringは暗号化されているので、WithDecryption=Trueを付けないと
    # 暗号化されたままの文字列が返ってきてしまう
    response = ssm.get_parameter(Name=parameter_name, WithDecryption=True)
    return response["Parameter"]["Value"]

# モジュールが読み込まれた瞬間(=Lambdaのコールドスタート時)に1回だけ実行される。
# リクエストが来るたびに毎回SSMへ問い合わせると、その分だけ遅くなるし
# API呼び出し回数も増えてしまうため、ここでキャッシュしておく
API_KEY = get_ssm_parameter("/myapp/API_KEY")
```

ここでの注目点は、`get_ssm_parameter`をハンドラーの中ではなく、ファイルの一番上(グローバルスコープ)で呼んでいることです。AWS Lambdaは一度立ち上がったコンテナをしばらく再利用する仕組みになっていて、グローバルスコープに書いたコードは「コンテナが新しく立ち上がったとき(コールドスタート)」にしか実行されません。もしこれをハンドラー関数の中に書いてしまうと、リクエストが来るたびにSSMへ問い合わせに行くことになり、レスポンスが遅くなったりAPIの呼び出し回数の上限に近づいたりします。Lambda以外の普通のサーバーで動かす場合でも、起動時に1回だけ取得してあとはメモリに持っておくという考え方は同じです。

`WithDecryption=True`を忘れると、`SecureString`で保存した値が暗号化されたまま返ってくるので、値がおかしいと思ったらまずここを確認すると良いです。

参考: [get_parameter(boto3公式ドキュメント)](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/ssm/client/get_parameter.html)

### 実行する前に確認しておくこと

ローカルで動かす場合は、AWS CLIの認証情報(`aws configure`で設定したもの)がそのまま使われます。Lambdaで動かす場合は、実行ロールに`ssm:GetParameter`(読み込み側)や`ssm:PutParameter`(登録側)の権限が付いているかを確認してください。権限が足りないと`AccessDeniedException`という分かりやすいエラーが返ってくるので、そこまで悩む場面ではないはずです。

### まとめ

- `.env`はあくまでSSMへ値を送るための作業用ファイルとして使い、Git管理からは外す
- 登録は`put_parameter`、読み込みは`get_parameter`という対になる関数で行う
- 秘密情報は`Type="SecureString"`で保存し、読み込み側では`WithDecryption=True`を付ける
- 読み込み処理はハンドラーの外(グローバルスコープ)に置いて、無駄な呼び出しを減らす

一度この形を作っておくと、新しいAPIキーが増えたときも「`.env`に1行追加して登録スクリプトを回すだけ」で済むようになります。
