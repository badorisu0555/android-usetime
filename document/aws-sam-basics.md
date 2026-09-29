# AWS SAMの基本と、実際のテンプレートでの使い方

## この記事について

子育て世代向けに、毎朝その日役立ちそうな情報をLINEで届ける、というアプリを個人開発しています。バックエンドはAWS Lambdaで組んでいて、そのインフラ一式(Lambda関数、DynamoDB、CloudWatchアラームなど)を1つのテンプレートファイルにコードとして定義しています。

このテンプレートは、ファイル名こそ「CloudFormation」ですが、実は**AWS SAM(Serverless Application Model)**のテンプレートです。冒頭に次の1行があるかどうかで見分けられます。

```json
"Transform": "AWS::Serverless-2016-10-31"
```

この記事では、AWS初心者向けに、

- SAMとは何か、使うとどんなメリットがあるのか
- CloudFormationとの違いは何か
- 実際に試したCLI操作(`sam build` / `sam deploy`など)が何をしているのか

を順番に説明します。

## SAMとは何か

SAM(Serverless Application Model)は、AWSが提供している、**Lambdaを中心としたサーバーレス構成を、CloudFormationより少ない記述量で書けるようにする仕組み**です。

CloudFormationは「AWSの各種リソースをコードで定義して、まとめてデプロイできるようにするサービス」ですが、Lambda関数1つを普通にCloudFormationで書こうとすると、

- Lambda関数本体(`AWS::Lambda::Function`)
- 実行時に使うIAMロール(`AWS::IAM::Role`)
- コードをどこからデプロイするか(S3へのアップロード)

などを、それぞれ自分で細かく定義する必要があります。SAMは、これらのよくあるパターンを`AWS::Serverless::Function`のような**専用リソースタイプ**にまとめ、短く書けるようにしたものです。

冒頭の`Transform`行がポイントで、これは「このテンプレートはSAMの書き方で書かれているので、デプロイ前に通常のCloudFormationの書き方に変換してください」という指示です。つまりSAMは、独立した別サービスではなく、**CloudFormationの拡張機能**という位置づけになります。

## SAMを使うメリット

### 1. 記述量が減る

先ほどのアプリの、毎朝コンテンツを配信するLambda関数を例にすると、SAMではこれだけの記述でLambda関数を定義できます。

```json
"Lambda1Function": {
  "Type": "AWS::Serverless::Function",
  "Properties": {
    "FunctionName": { "Fn::Sub": "${ProjectName}-deliver-contents" },
    "CodeUri": "../app/deliver_contents/",
    "Handler": "main.lambda_handler",
    "Runtime": "python3.12",
    "Timeout": 120,
    "MemorySize": 512,
    "Role": { "Fn::GetAtt": ["Lambda1ExecutionRole", "Arn"] }
  }
}
```

特に`CodeUri`が便利な点です。ここにはローカルのソースコードのフォルダパス(`../app/deliver_contents/`)を指定するだけでよく、「このコードをZIPにしてS3にアップロードし、そのS3のパスをLambdaに紐付ける」という作業を、SAMのCLIツールが自動でやってくれます。通常のCloudFormationの`AWS::Lambda::Function`では、このS3のパスを自分で用意する必要があります。

### 2. サーバーレス用のCLIツール(SAM CLI)が使える

SAMには`sam`コマンドという専用のCLIツールがあり、次のようなコマンドが使えます。

- `sam build`: Lambdaのコードと依存ライブラリをまとめる
- `sam deploy`: ビルドした内容をAWSにデプロイする
- `sam local invoke`: Lambda関数を**手元のPCで**実行して動作確認する

特に`sam local invoke`は、「AWSに実際デプロイしなくても、Dockerコンテナ上でLambdaと近い環境を再現してテストできる」という点で、開発中に何度も試行錯誤する場面で助かります。

### 3. サーバーレスによくある構成が短く書ける

Lambda関数以外にも、`AWS::Serverless::Api`(API Gateway)や`AWS::Serverless::LayerVersion`(Lambda Layer)など、サーバーレス構成でよく使うリソースがSAM独自の書き方で用意されています。今回のアプリでも、LINEの公式SDKをLambda Layerとして読み込むのにこの書き方を使っています。

## CloudFormationとの違い

SAMは「別物」ではなく「CloudFormationの上に乗っている拡張」なので、違いは次のように整理できます。

| 観点 | CloudFormation | SAM |
| --- | --- | --- |
| 立ち位置 | AWSのリソースをコードで管理する基盤サービス | CloudFormationの拡張(`Transform`で変換される) |
| 書き方 | 全リソースをAWS本来のリソースタイプで細かく書く | Lambda・API Gateway・DynamoDBなどサーバーレス定番構成を専用リソースタイプで短く書ける |
| CLIツール | AWS CLI(`aws cloudformation ...`) | SAM CLI(`sam build` / `sam deploy` / `sam local invoke`など) |
| ローカル実行 | 標準機能では無い | `sam local invoke`でLambdaをローカル実行できる |
| 使える場面 | あらゆるAWSリソース | Lambda中心のサーバーレス構成 |

一番の理解のポイントは、**SAMのテンプレートは最終的にCloudFormationのテンプレートに変換されてからデプロイされる**、という点です。実際、`Lambda1Function`(`AWS::Serverless::Function`)も、デプロイ時には内部で`AWS::Lambda::Function`とS3上のコード配置に変換されています。そのため、SAM独自のリソースタイプと、通常のCloudFormationのリソースタイプが**同じテンプレートの中に混在していても問題ありません**。実際、今回のテンプレートでも、`AWS::Serverless::Function`(SAM)と`AWS::DynamoDB::Table`や`AWS::CloudWatch::Alarm`(通常のCloudFormation)が同じファイルの中に共存しています。

## 実際に試したCLI操作

実際にデプロイする際に使った、次の3つのコマンドが何をしているかを説明します。

```powershell
$env:PYTHONUTF8="1"; $env:PYTHONIOENCODING="utf-8"
```

SAM CLIはPython製のツールです。Windows環境では、PowerShellの既定の文字コードが日本語ロケール(cp932)になっていることがあり、そのままだとテンプレートやログに含まれる文字の扱いでエラーが出ることがあります。このコマンドは、それを避けるために、Pythonの入出力を強制的にUTF-8として扱わせる環境変数を、コマンド実行前にセットしています。

- `PYTHONUTF8="1"`: Pythonの「UTF-8モード」を有効にする。OS側のロケール設定に関係なく、標準入出力などをUTF-8として扱うようになる
- `PYTHONIOENCODING="utf-8"`: 標準入出力(コンソールへの出力など)のエンコーディングを明示的にUTF-8に指定する

`;`はPowerShellで複数のコマンドを1行にまとめて実行するための区切り文字です。この2つの環境変数は、そのPowerShellのウィンドウを開いている間だけ有効です。

```powershell
sam build --template CloudFormation.json
```

`sam build`は、テンプレートに書いたLambda関数のコード(`CodeUri`で指定したフォルダ)と、必要な依存ライブラリをまとめて、デプロイ用の成果物を作るコマンドです。作られた成果物は`.aws-sam`フォルダの中に置かれます。

`sam build`はデフォルトでは`template.yaml`(または`template.yml`)という名前のファイルを探しにいきますが、今回のテンプレートは`CloudFormation.json`という名前で作っているため、`--template`オプションでファイル名を明示的に指定しています。

```powershell
sam deploy
```

`sam deploy`は、`sam build`で作った成果物を、実際にAWS上にデプロイするコマンドです。内部的には、Lambdaのコードなどを一度S3にアップロードし、それを踏まえたCloudFormationのテンプレートを使って、CloudFormationの「変更セット」を作成・実行します。つまり、「CloudFormationとの違い」で説明した「SAMのテンプレートは最終的にCloudFormationに変換されてからデプロイされる」という流れを、実際に手を動かして反映させているのがこのコマンドです。

## まとめ

- SAMは、CloudFormationの拡張機能で、Lambda中心のサーバーレス構成を短く書けるようにするもの
- `Transform: AWS::Serverless-2016-10-31`の1行があるテンプレートがSAM
- `sam build` / `sam deploy` / `sam local invoke`など専用CLIが使えるのもメリット
- テンプレートファイル名が`template.yaml`でない場合は、`sam build --template <ファイル名>`のように明示的に指定する
