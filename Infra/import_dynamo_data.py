import pandas as pd
import boto3
from datetime import datetime, timedelta, timezone
import numpy as np
import os
from dotenv import load_dotenv


def get_ssm_parameter(parameter_name):
    ssm = boto3.client("ssm")
    response = ssm.get_parameter(Name=parameter_name, WithDecryption=True)
    return response["Parameter"]["Value"]

def dynamo_batch_write(import_data_df, table_name, region_name='ap-northeast-1'):
    data = import_data_df.to_dict(orient='records')
    dynamodb = boto3.resource('dynamodb', region_name=region_name)
    table = dynamodb.Table(table_name)

    with table.batch_writer() as batch:
        for item in data:
            item_not_has_nan = {
                key: item[key] for key in item
                if not (isinstance(item[key], float) and np.isnan(item[key]))
            }
            batch.put_item(Item=item_not_has_nan)
    print(f"Successfully wrote {len(data)} items to {table_name} table.")
    return "Successfully wrote to DynamoDB"

table_name_list = [
    "childcare-info-table1-deliverycontent",
    "childcare-info-table2-userprofile",
    "childcare-info-table3-categoryscore",
]

JST = timezone(timedelta(hours=9))
LINE_user_id = get_ssm_parameter("/childcare-info/LINE_USER_ID")

# ---- Table1: 配信コンテンツ管理 ----
now_jst = datetime.now(JST)
delivered_at_str = now_jst.strftime("%Y-%m-%dT%H:%M:%S+09:00")
delivered_date_str = now_jst.strftime("%Y%m%d")
ttl_epoch = int((now_jst + timedelta(days=7)).timestamp())  # 設計書: 1週間

import_data1 = {
    'delivery_id': [
        f'{LINE_user_id}#{delivered_date_str}#01',
        f'{LINE_user_id}#{delivered_date_str}#02',
    ],
    'user_id': [LINE_user_id, LINE_user_id],
    'delivered_at': [delivered_at_str, delivered_at_str],
    'category': ['睡眠', '遊び・おでかけ'],
    'body': [
        'この頃の睡眠はおおよそ10時間ほどです。暑い日は途中で目が覚めやすいので、エアコンで室温と湿度を整えてあげましょう。汗をかいたら着替えさせ、寝る前の水分補給も忘れずに行うと、寝苦しさが和らぎます。',
        '室内でできる感覚遊びがおすすめの時期です。新聞紙をちぎる、粘土をこねる、シール貼りをするなど、指先を使う遊びは手先の発達にも良い影響を与えます。雨の日でも飽きずに楽しめる遊びとして取り入れてみてください。',
    ],
    'summary': ['夏場の睡眠環境調整について', '室内での感覚遊びの提案'],
    'score': [4, 3],  # デフォルト値ではなく、既に評価済みのテストデータとして投入
    'ttl': [ttl_epoch, ttl_epoch],
}

# ---- Table2: ユーザーパーソナルデータ ----
import_data2 = {
    'user_id': [LINE_user_id],
    'children': [[
        {'child_name': '子供1', 'birth_date': '2024-12-19'},
        {'child_name': '子供2', 'birth_date': '2026-08-10'},
    ]],
    'values': ['子供が順調に成長しているかが気になる。どんな遊びをすればよいか悩みがち'],
}

# ---- Table3: カテゴリ別累積スコア ----
import_data3 = {
    'user_id': [LINE_user_id],
    'sleep_sum': [18], 'sleep_count': [4],          # 平均4.5
    'food_sum': [12], 'food_count': [4],            # 平均3.0(TOP5除外テスト用)
    'growth_sum': [15], 'growth_count': [4],        # 平均3.75
    'health_sum': [9], 'health_count': [3],         # 平均3.0(除外テスト用)
    'safety_sum': [8], 'safety_count': [2],         # 平均4.0
    'play_sum': [10], 'play_count': [3],            # 平均3.33
    'daycare_sum': [6], 'daycare_count': [2],       # 平均3.0(除外テスト用)
    'parent_care_sum': [4], 'parent_care_count': [2],  # 平均2.0
}

import_data_df1 = pd.DataFrame(import_data1)
import_data_df2 = pd.DataFrame(import_data2)
import_data_df3 = pd.DataFrame(import_data3)

dynamo_batch_write(import_data_df1, table_name_list[0], region_name='ap-northeast-1')
dynamo_batch_write(import_data_df2, table_name_list[1], region_name='ap-northeast-1')
dynamo_batch_write(import_data_df3, table_name_list[2], region_name='ap-northeast-1')