import logging
import urllib.parse  # postback.dataの "delivery_id=xxx&score=5" 形式を分解するために使う

import boto3
from botocore.exceptions import ClientError
from linebot.v3 import WebhookParser
from linebot.v3.exceptions import InvalidSignatureError

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def get_ssm_parameter(parameter_name):
    ssm = boto3.client("ssm")
    response = ssm.get_parameter(Name=parameter_name, WithDecryption=True)
    return response["Parameter"]["Value"]


CHANNEL_SECRET = get_ssm_parameter("/childcare-info/LINE_CHANNEL_SECRET")
# WebhookParserはコールドスタート時(モジュール読み込み時)に1回だけ作る。
# lambda_handlerの中で毎回作ると、リクエストのたびに無駄な初期化コストがかかるため
parser = WebhookParser(CHANNEL_SECRET)

TABLE1_NAME = "childcare-info-table1-deliverycontent"
TABLE3_NAME = "childcare-info-table3-categoryscore"
dynamodb = boto3.resource("dynamodb")
# Table1/Table3のTableオブジェクトも同様にハンドラー外(コールドスタート時)で作り、使い回す
table1 = dynamodb.Table(TABLE1_NAME)
table3 = dynamodb.Table(TABLE3_NAME)

# Table1にはcategoryを日本語("睡眠"等)で保存しているが、
# Table3のカラム名は英語(sleep_sum等)なので対応表が必要。
# Lambda詳細設計.mdの方針通り、いったんハードコーディングで対応
CATEGORY_JA_TO_EN = {
    "睡眠": "sleep",
    "食事・栄養": "food",
    "発達・成長": "growth",
    "健康・体調管理": "health",
    "安全": "safety",
    "遊び・おでかけ": "play",
    "保育園・制度": "daycare",
    "親のケア": "parent_care",
}

def lambda_handler(event, context):
    # Function URL経由のイベントは、生のリクエストボディが event["body"] に文字列で入ってくる
    raw_body = event.get("body", "")
    headers = event.get("headers", {})
    # x-line-signatureヘッダーには、LINEがチャネルシークレットでボディをHMAC-SHA256署名した値が入っている。
    # これをparser.parse()に渡すことで、リクエストが本当にLINEプラットフォームから来たものかを検証できる
    signature = headers.get("x-line-signature", "")

    try:
        # parser.parse()は「署名検証」と「JSONパース」を1回でやってくれる。
        # 署名検証に失敗すると InvalidSignatureError が発生する仕組み
        events = parser.parse(raw_body, signature)
    except InvalidSignatureError:
        # 署名が一致しない = LINE以外からの不正なリクエストの可能性があるため、
        # 処理を進めずに400を返して終了する
        logger.info("署名検証NG")
        return {"statusCode": 400, "body": "Invalid Signature"}

    logger.info("署名検証OK")

    for e in events:
        logger.info(f"event.type = {e.type}")
        # postback以外のイベント(友だち追加等)も理論上飛んでくる可能性があるため、
        # postbackイベントだけを処理対象として絞り込む
        if e.type == "postback":
            handle_postback(e)

    return {
        "statusCode": 200,
        "body": "OK"
    }

def handle_postback(e):
    data = e.postback.data
    logger.info(f"postback.data = {data}")

    # data は "delivery_id=xxx&score=5" のようなクエリ文字列形式なので、
    # urllib.parse.parse_qsl で key=value のペアに分解する
    parsed = dict(urllib.parse.parse_qsl(data))
    delivery_id = parsed.get("delivery_id")
    score_str = parsed.get("score")

    if not delivery_id or not score_str:
        # 想定外のdata形式の場合、後続処理でエラーになる前にここで止めてログに残す
        logger.error(f"postback.dataの形式が不正:{data}")
        return

    score = int(score_str)  # postback.dataは文字列なので、DynamoDBのNumber型として保存するためintに変換

    try:
        # ConditionExpressionは「更新して良い条件」を指定するもの。
        # attribute_not_exists(rated): Lambda1のput_item時点ではratedを設定していないので、
        #   初回評価時はこの条件でヒットする
        # OR rated = :false: 将来的に明示的にfalseを入れる運用に変えても対応できるようにしておく
        # どちらも満たさない(=rated=trueが既に入っている)場合は例外が発生し、更新は行われない

        response = table1.update_item(
            Key={"delivery_id": delivery_id},
            UpdateExpression="SET score = :score, rated = :true",
            ConditionExpression="attribute_not_exists(rated) OR rated = :false",
            ExpressionAttributeValues={":score": score, ":true": True, ":false": False},
            ReturnValues="ALL_NEW"
        )

    except ClientError as ex:
        if ex.response["Error"]["Code"] == "ConditionalCheckFailedException":
            # 条件を満たさなかった = 既に評価済み。
            # LINEの再送や、ユーザーが評価後に別の点数を押した場合にここに来る。
            # Table1・Table3どちらも更新せず、エラーではなく正常なスキップとして扱う
            logger.info(f"既に評価済みのためスキップ: delivery_id={delivery_id}")
            return
        logger.error(f"Table1更新でエラー: delivery_id={delivery_id}, {ex}")
        return 

    logger.info(f"Table1更新完了:delivery_id={delivery_id}, score={score}")

    # Table1のcategoryは日本語で保存されているため、Table3のカラム名(英語)に変換する
    category_ja = response["Attributes"].get("category")
    category_en = CATEGORY_JA_TO_EN.get(category_ja)
    if category_en is None:
        # 対応表にないcategoryが来た場合、Table3の更新はできないのでここで止める
        logger.error(f"未知のカテゴリ名:{category_ja}")
        return

    # delivery_idは "userId#YYYYMMDD#配信no" 形式なので、先頭のuserId部分だけ取り出す
    user_id = delivery_id.split("#")[0]
    # ADDアクションを使うことで、「取得→加算→書き込み」を1回のアトミックな操作で行える。
    # 同時に複数の評価が来ても、値が上書きされず正しく合算される
    table3.update_item(
        Key={"user_id": user_id},
        UpdateExpression=f"ADD {category_en}_sum :score, {category_en}_count :one",
        ExpressionAttributeValues={":score": score, ":one": 1}
    )

    logger.info(f"Table3更新完了: user_id={user_id}, category={category_en}")