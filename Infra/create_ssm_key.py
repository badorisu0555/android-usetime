import os
import boto3
from dotenv import load_dotenv

load_dotenv()

ssm = boto3.client("ssm",region_name="ap-northeast-1")

PARAMETERS = {
    "/childcare-info/LINE_CHANNEL_ACCESS_TOKEN": "LINE_CHANNEL_ACCESS_TOKEN",
    "/childcare-info/CLAUDE_API_KEY": "CLAUDE_API_KEY",
    "/childcare-info/LINE_USER_ID": "LINE_USER_ID",
    "/childcare-info/LINE_CHANNEL_SECRET": "LINE_CHANNEL_SECRET"
}

def register_parameters():
    for ssm_name , env_key in PARAMETERS.items():
        value = os.environ.get(env_key)
        if not value:
            print(f"警告{env_key}が見つかりません。")
            continue
        ssm.put_parameter(
            Name=ssm_name,
            Value=value,
            Type="SecureString",
            Overwrite=True
        )
        print(f"{ssm_name}をSSMパラメータストアに登録しました。")

if __name__ == "__main__":
    register_parameters()