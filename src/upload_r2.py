import os
import boto3
from logger import logger
from botocore.exceptions import NoCredentialsError, PartialCredentialsError, ClientError
from config import BUCKET_NAME, ACCESS_KEY, SECRET_KEY, ENDPOINT_URL

def object_exists(s3_client, bucket_name, object_key):
    try:
        s3_client.head_object(Bucket=bucket_name, Key=object_key)
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            return False
        else:
            raise e
    return True


def upload_files(local_folder, bucket_name=BUCKET_NAME, check_exist=True):
    session = boto3.session.Session()
    s3_client = session.client(
        service_name="s3",
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        endpoint_url=ENDPOINT_URL,
    )
    for root, _, files in os.walk(local_folder):
        for file in files:
            local_path = os.path.join(root, file)
            relative_path = os.path.relpath(local_path, local_folder)
            s3_path = local_folder.replace("../output/", "") + "/" + relative_path.replace(os.sep, "/")
            if check_exist and object_exists(s3_client, bucket_name, s3_path):
                logger.info(f"===> file exist skip {s3_path}")
                continue
            try:
                s3_client.upload_file(local_path, bucket_name, s3_path)
                logger.info(f"===> Successfully uploaded {s3_path}")
            except FileNotFoundError:
                logger.error(f"===> File not found: {local_path}")
            except NoCredentialsError:
                logger.error("===> Credentials not available")
            except PartialCredentialsError:
                logger.error("===> Incomplete credentials provided")
            except Exception as e:
                logger.error(f"===> Failed to upload {s3_path}: {e}")


upload_files(local_folder="../output/rss", bucket_name=BUCKET_NAME, check_exist=False)
upload_files(local_folder="../output/bilibili-season", bucket_name=BUCKET_NAME)
# upload_files(local_folder="../output/bilibili-series", bucket_name=BUCKET_NAME)

