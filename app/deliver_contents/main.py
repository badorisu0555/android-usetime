import os
import json
import re
import requests
from datetime import datetime, timedelta, timezone, date
from logging import getLogger, StreamHandler, Formatter, DEBUG

import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key

from dateutil.relativedelta import relativedelta
from langchain_core.prompts import PromptTemplate
import anthropic

logger = getLogger(__name__)
handler = StreamHandler()
handler.setLevel(DEBUG)
formatter = Formatter('[%(levelname)s%(asctime)s%(message)s%(name)s]')
handler.setFormatter(formatter)
logger.setLevel(DEBUG)
logger.addHandler(handler)

logger.info('Creating childhood content is starting...')
JST = timezone(timedelta(hours=9))

TABLE1_NAME = "childcare-info-table1-deliverycontent"
TABLE2_NAME = "childcare-info-table2-userprofile"
TABLE3_NAME = "childcare-info-table3-categoryscore"

CATEGORY_NAME_MAP = {
    "sleep": "睡眠",
    "food": "食事・栄養",
    "growth": "発達・成長",
    "health": "健康・体調管理",
    "safety": "安全",
    "play": "遊び・おでかけ",
    "daycare": "保育園・制度",
    "parent_care": "親のケア",
}


def get_ssm_parameter(parameter_name):
    ssm = boto3.client("ssm")
    response = ssm.get_parameter(Name=parameter_name, WithDecryption=True)
    return response["Parameter"]["Value"]


def extract_json(text):
    if text is None:
        return None
    match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if match:
        return match.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]
    return text


def create_response(prompt_text, today, deliverycontent, userprofile, categoryscore):
    client = anthropic.Anthropic()
    prompt = PromptTemplate(
        input_variables=["today", "deliverycontent", "userprofile", "categoryscore"],
        template=prompt_text)
    prompt = prompt.format(today=today, deliverycontent=deliverycontent, userprofile=userprofile, categoryscore=categoryscore)

    message = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=5000,
        messages=[
            {
                "role": "user",
                "content": prompt,
            }
        ]
        # ,tools = [{"type":"web_search_20260318","name":"web_search"}] #コスト削減のためwebsearchは一旦外す。必要に応じて再度追加すること。
    )

    answer_text = None
    for block in message.content:
        if block.type == "text":
            answer_text = block.text

    return extract_json(answer_text)


def create_childhood_content(deliverycontent, userprofile, categoryscore):
    os.environ["ANTHROPIC_API_KEY"] = get_ssm_parameter("/childcare-info/CLAUDE_API_KEY")

    prompt_path = os.path.join(os.path.dirname(__file__), "prompt.txt")
    with open(prompt_path, "r", encoding="utf-8") as f:
        prompt_text = f.read()
    today = datetime.now(JST).strftime("%Y-%m-%d")
    answer = create_response(prompt_text, today, deliverycontent, userprofile, categoryscore)
    return answer


def get_dynamo_data(table_name, user_id, index_name=None, days=None, region_name='ap-northeast-1'):
    logger.info(f'Getting data from DynamoDB table: {table_name}, user_id: {user_id}, index_name: {index_name}, days: {days}')
    dynamodb = boto3.resource('dynamodb', region_name=region_name)
    table = dynamodb.Table(table_name)

    if index_name is not None:
        if days is None:
            raise ValueError("query時はdaysを指定してください。")

        now_jst = datetime.now(JST)
        start_time_str = (now_jst - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S+09:00")
        end_time_str = now_jst.strftime("%Y-%m-%dT%H:%M:%S+09:00")

        try:
            response = table.query(
                IndexName=index_name,
                KeyConditionExpression=(
                    Key("user_id").eq(user_id) &
                    Key("delivered_at").between(start_time_str, end_time_str)
                )
            )
        except ClientError as e:
            # 読み取りに失敗した日はコンテンツを生成できないため、配信自体をスキップする方針。
            # ALARM: プレフィックスはCloudWatch Logsメトリクスフィルタで拾うための目印(他の失敗と区別するため)
            logger.error(f"[ALARM:DYNAMO_READ_FAILED] table={table_name}, user_id={user_id}, error={e}")
            raise

        items = response.get('Items', [])
        return items

    else:
        try:
            response = table.get_item(Key={"user_id": user_id})
        except ClientError as e:
            logger.error(f"[ALARM:DYNAMO_READ_FAILED] table={table_name}, user_id={user_id}, error={e}")
            raise
        return response.get("Item")


def calc_age_month(birth_date):
    today = date.today()
    birth_date = date.fromisoformat(birth_date)
    date_difference = relativedelta(today, birth_date)
    return date_difference.years * 12 + date_difference.months


def process_category_scores(categoryscore):
    categories = set()
    for key in categoryscore:
        if key.endswith("_sum"):
            categories.add(key[:-len("_sum")])
        elif key.endswith("_count"):
            categories.add(key[:-len("_count")])
    result = {
        cat: float(categoryscore[f"{cat}_sum"]) / float(categoryscore[f"{cat}_count"])
        for cat in categories
    }
    categoryscore = sorted(result.items(), key=lambda x: x[1], reverse=True)[:3]
    return [{"カテゴリー": CATEGORY_NAME_MAP[cat], "スコア": score} for cat, score in categoryscore]


def build_delivery_items(generated_content, user_id):
    # LINE表示用のitemsと、DynamoDB保存用のdb_itemsを組み立てるだけの処理(DB通信は行わない)。
    # DynamoDBへの保存(save_delivery_contents)を分離しておくことで、保存に失敗してもLINE配信は続行できるようにする。
    # score, ttlは初期状態(未評価)として登録しておき、ユーザーの評価postback受信時(Lambda2側)に更新する想定
    now_jst = datetime.now(JST)
    delivered_at_str = now_jst.strftime("%Y-%m-%dT%H:%M:%S+09:00")
    delivered_date_str = now_jst.strftime("%Y%m%d")
    ttl_epoch = int((now_jst + timedelta(days=7)).timestamp())

    items = []
    db_items = []
    seq = 1
    for child in generated_content["children"]:
        logger.debug(f"[DEBUG] Processing child: {child.get('child_name', 'Unknown')}")
        child_name = child["child_name"]
        for content in child["contents"]:
            delivery_id = f"{user_id}#{delivered_date_str}#{seq:02d}"
            db_items.append({
                "delivery_id": delivery_id,
                "user_id": user_id,
                "delivered_at": delivered_at_str,
                "category": content["category"],
                "body": content["body"],
                "summary": content["summary"],
                "ttl": ttl_epoch,
            })
            items.append({
                "delivery_id": delivery_id,
                "header": f"{child_name}・{content['category']}",
                "body": content["body"],
            })
            seq += 1
    return items, db_items


def save_delivery_contents(db_items):
    dynamodb = boto3.resource('dynamodb', region_name='ap-northeast-1')
    table = dynamodb.Table(TABLE1_NAME)

    try:
        for item in db_items:
            table.put_item(Item=item)
    except ClientError as e:
        # 書き込みに失敗してもLINE配信は止めない方針のため、ここでは例外を再送出しない。
        # (代わりにpostbackでのスコア更新は効かなくなるが、情報が届くことを優先する)
        # ALARM: プレフィックスはCloudWatch Logsメトリクスフィルタで拾うための目印(他の失敗と区別するため)
        logger.error(f"[ALARM:DYNAMO_WRITE_FAILED] table={TABLE1_NAME}, error={e}")


def build_score_button(delivery_id, score):
    return {
        "type": "box",
        "layout": "vertical",
        "cornerRadius": "6px",
        "backgroundColor": "#EFEFEF",
        "paddingTop": "4px",
        "paddingBottom": "4px",
        "action": {
            "type": "postback",
            "label": str(score),
            "data": f"delivery_id={delivery_id}&score={score}"
        },
        "contents": [
            {"type": "text", "text": str(score), "align": "center", "size": "xs", "color": "#4A90D9"}
        ]
    }


def format_to_flex_carousel(delivery_items):
    # --- 文字列で届いた場合はJSONとしてパースする ---
    if isinstance(delivery_items, str):
        try:
            logger.debug("[DEBUG] Data is string type. Attempting json.loads...")
            delivery_items = json.loads(delivery_items)
        except Exception as e:
            logger.error(f"[ERROR] Failed to parse string to JSON: {e}")
            return None

    # 型チェック：この時点でリスト（list）になっていない場合は異常
    if not isinstance(delivery_items, list):
        logger.error(f"[ERROR] Expected list but got {type(delivery_items)}")
        return None

    if len(delivery_items) == 0:
        logger.error("[ERROR] delivery_items is empty")
        return None

    logger.debug(f"[DEBUG] Successfully converted to list. Items: {len(delivery_items)}")

    bubbles = []
    for item in delivery_items[:12]:
        # 必要なフィールドを get() で安全に取得
        bubble = {
            "type": "bubble",
            "size": "mega",
            "header": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": "#4A90D9",
                "paddingAll": "12px",
                "contents": [
                    {"type": "text", "text": item.get("header", ""), "color": "#FFFFFF", "weight": "bold", "size": "md", "wrap": True}
                ]
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "paddingAll": "16px",
                "contents": [
                    {"type": "text", "text": item.get("body", ""), "wrap": True, "size": "sm", "color": "#333333"}
                ]
            },
            "footer": {
                "type": "box",
                "layout": "horizontal",
                "spacing": "xs",
                "paddingAll": "8px",
                "contents": [build_score_button(item["delivery_id"], score) for score in range(1, 6)]
            }
        }
        bubbles.append(bubble)

    return {"type": "carousel", "contents": bubbles}


def push_line_message(user_id, flex_contents):
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    data = {
        "to": user_id,
        "messages": [
            {
                "type": "text",
                "text": "本日の子育てのお役立ち情報を配信いたします！良いと感じたら下のボタンから評価をお願いします。"
            },
            {
                "type": "flex",
                "altText": "本日の育児情報が届きました",
                "contents": flex_contents
            }
        ]
    }

    url = "https://api.line.me/v2/bot/message/push"
    response = requests.post(url, headers=headers, json=data)

    if response.status_code == 200:
        logger.info("Flex Messageが正常に送信されました")
    else:
        # ALARM: プレフィックスはCloudWatch Logsメトリクスフィルタで拾うための目印(他の失敗と区別するため)
        logger.error(f"[ALARM:LINE_DELIVERY_FAILED] status={response.status_code}, body={response.text}")
        # ここで例外を送出しないと、LINE配信に失敗してもLambdaは正常終了(exit code 0)扱いになり、
        # CloudWatchアラームがLambdaの Errors メトリクスで検知できなくなるため、あえて呼び出し元に伝播させる
        raise RuntimeError(f"LINE API Error: status={response.status_code}, body={response.text}")

    return response


def lambda_handler(event, context):
    logger.info('Starting main execution...')
    line_user_id = get_ssm_parameter("/childcare-info/LINE_USER_ID")
    global LINE_CHANNEL_ACCESS_TOKEN
    LINE_CHANNEL_ACCESS_TOKEN = get_ssm_parameter("/childcare-info/LINE_CHANNEL_ACCESS_TOKEN")

    table_name_list = [TABLE1_NAME, TABLE2_NAME, TABLE3_NAME]

    # Table1: delivery_id(PK)とは別に、「あるuser_idの直近N日分」を検索したいので、GSI1(PK: user_id, SK: delivered_at)を作成しました。そのためindex_nameを指定してqueryする必要があります。
    logger.info('Getting delivery content from DynamoDB...')
    deliverycontent = get_dynamo_data(table_name_list[0], line_user_id, index_name="GSI1", days=5)
    deliverycontent = [{"サマリー": d["summary"], "カテゴリー": d["category"]} for d in deliverycontent]

    # 以下の二つは、user_idで一意に取得できるので、index_nameは不要でOKです。
    logger.info('Getting user profile from DynamoDB...')
    userprofile = get_dynamo_data(table_name_list[1], line_user_id)
    userprofile = {
        "価値観": userprofile["values"],
        "子供の情報": [
            {"子供の名前": c["child_name"], "月齢": calc_age_month(c["birth_date"])} for c in userprofile["children"]
        ]}

    logger.info('Getting category scores from DynamoDB...')
    categoryscore = get_dynamo_data(table_name_list[2], line_user_id)
    categoryscore = process_category_scores(categoryscore)

    logger.info('Creating childhood content...')
    answer = create_childhood_content(deliverycontent, userprofile, categoryscore)
    generated_content = json.loads(answer)

    logger.info('Building delivery items...')
    delivery_items, db_items = build_delivery_items(generated_content, line_user_id)

    logger.info('Writing delivery contents to DynamoDB...')
    save_delivery_contents(db_items)

    logger.info('Formatting delivery items to Flex carousel...')
    flex_contents = format_to_flex_carousel(delivery_items)

    if flex_contents is None:
        logger.info("[ABORT] No contents to send. Skipping LINE API call.")
    else:
        logger.info('Pushing message to LINE...')
        push_line_message(line_user_id, flex_contents)

    return {"statusCode": 200, "body": "OK"}
