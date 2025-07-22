import os
import tempfile
import pydicom
import traceback
import numpy as np
from skimage import measure
import datetime
from stl import mesh

from firebase_admin import initialize_app, storage as admin_storage, credentials
from firebase_functions import options, https_fn, storage_fn
from firebase_functions.options import set_global_options
from google.cloud import storage

set_global_options(region=options.SupportedRegion.US_WEST1, timeout_sec=540)

initialize_app()

@https_fn.on_request(
    memory=options.MemoryOption.MB_256,
    timeout_sec=30,  # タイムアウトを30秒に設定
)
def check_job_status(req: https_fn.Request) -> https_fn.Response:
    """
    処理ジョブのステータスを確認し、完了していればSTLのダウンロードURLを返す。(REST API)
    """
    # CORSプリフライトリクエストに対応
    if req.method == "OPTIONS":
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "Access-Control-Max-Age": "3600",
        }
        return https_fn.Response("", status=204, headers=headers)

    # CORSヘッダーをすべてのレスポンスに設定
    cors_headers = {"Access-Control-Allow-Origin": "*"}

    # POST以外のメソッドは許可しない
    if req.method != "POST":
        return https_fn.Response("Method Not Allowed", status=405, headers=cors_headers)

    # JSONボディからuploadIdを取得
    upload_id = None
    if req.is_json:
        upload_id = req.get_json(silent=True).get("uploadId")

    if not upload_id:
        return https_fn.Response(
            '{"error": "uploadId is required in JSON body."}',
            status=400,
            headers=cors_headers,
            mimetype="application/json",
        )

    bucket_name = "dicom2stl-97760.firebasestorage.app"
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)

    result_stl_path = f"results/{upload_id}/model.stl"
    processing_flag_path = f"results/{upload_id}/_PROCESSING"
    error_flag_path = f"results/{upload_id}/_ERROR"

    stl_blob = bucket.blob(result_stl_path)
    processing_blob = bucket.blob(processing_flag_path)
    error_blob = bucket.blob(error_flag_path)

    response_data = {}
    if stl_blob.exists():
        # 完了：署名付きURLを生成
        url = stl_blob.generate_signed_url(
            version="v4",
            expiration=datetime.timedelta(minutes=15),
            method="GET",
        )
        response_data = {"status": "completed", "downloadUrl": url}
    elif error_blob.exists():
        # エラー
        error_message = error_blob.download_as_text()
        response_data = {"status": "error", "message": error_message}
    elif processing_blob.exists():
        # 処理中
        response_data = {"status": "processing"}
    else:
        response_data = {"status": "pending"}

    import json
    return https_fn.Response(
        json.dumps(response_data),
        headers=cors_headers,
        mimetype="application/json"
    )

# DICOMのような重い処理を扱うため、メモリとタイムアウト時間を増やします
@storage_fn.on_object_finalized(
    bucket="dicom2stl-97760.firebasestorage.app",
    memory=options.MemoryOption.GB_1)
def on_dicom_upload(event: storage_fn.CloudEvent) -> None:
    """
    Cloud StorageにファイルがアップロードされたときにSTL変換処理を起動するトリガー。
    """
    bucket_name = event.data.bucket
    file_path = event.data.name

    # 'uploads/' ディレクトリ以外のファイルは無視
    if not file_path.startswith("uploads/"):
        print(f"Ignoring file not in uploads/ directory: {file_path}")
        return

    # 処理済みのSTLファイルや中間ファイル自身をトリガーしないようにする
    if file_path.endswith(".stl") or os.path.basename(file_path) == "_PROCESSING":
        print(f"Ignoring meta/output file: {file_path}")
        return

    # uploadIdと各種パスを定義
    path_parts = file_path.split("/")
    # path should be: uploads/{userId}/{uploadId}/...
    if len(path_parts) < 4:
        print(f"Invalid file path structure: {file_path}")
        return
    user_id = path_parts[1]
    upload_id = path_parts[2]
    dicom_dir_prefix = f"uploads/{user_id}/{upload_id}/"
    processing_flag_path = f"results/{upload_id}/_PROCESSING"
    result_stl_path = f"results/{upload_id}/model.stl"

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)

    # ---- 多重実行防止 ----
    # 処理中フラグを確認し、存在すれば処理を中断
    flag_blob = bucket.blob(processing_flag_path)
    if flag_blob.exists():
        print(f"Processing already in progress for {upload_id}. Exiting.")
        return

    # 処理中フラグを立てる
    flag_blob.upload_from_string("")
    print(f"Set processing flag for {upload_id}")

    try:
        # --- DICOMファイルのダウンロード ---
        blobs = list(bucket.list_blobs(prefix=dicom_dir_prefix))
        dicom_blobs = [b for b in blobs if not b.name.endswith("/")]

        if not dicom_blobs:
            print(f"No DICOM files found in {dicom_dir_prefix}")
            error_blob = bucket.blob(f"results/{upload_id}/_ERROR")
            error_blob.upload_from_string(f"No DICOM files found in the specified folder: {dicom_dir_prefix}")
            return

        with tempfile.TemporaryDirectory() as tmpdir:
            local_dicom_paths = []
            for blob in dicom_blobs:
                destination_path = os.path.join(tmpdir, os.path.basename(blob.name))
                blob.download_to_filename(destination_path)
                local_dicom_paths.append(destination_path)

            print(f"Downloaded {len(local_dicom_paths)} files to {tmpdir}")

            # --- DICOM読み込みと3Dボリューム構築 ---
            slices = [pydicom.dcmread(p) for p in local_dicom_paths]
            slices.sort(key=lambda x: float(x.SliceLocation))

            # HU値に変換し、3Dボリュームを構築
            image_stack = np.stack(
                [s.pixel_array * s.RescaleSlope + s.RescaleIntercept for s in slices]
            )

            # --- 3Dメッシュ生成 (Marching Cubes) ---
            # 骨などを抽出するための閾値（この値は調整が必要）
            threshold = 300
            verts, faces, _, _ = measure.marching_cubes(image_stack, level=threshold)

            # --- STLファイル生成 ---
            stl_mesh = mesh.Mesh(np.zeros(faces.shape[0], dtype=mesh.Mesh.dtype))
            for i, f in enumerate(faces):
                for j in range(3):
                    stl_mesh.vectors[i][j] = verts[f[j], :]

            local_stl_path = os.path.join(tmpdir, "model.stl")
            stl_mesh.save(local_stl_path)
            print(f"Generated STL file at {local_stl_path}")

            # --- STLファイルのアップロード ---
            stl_blob = bucket.blob(result_stl_path)
            stl_blob.upload_from_filename(local_stl_path)
            print(f"Uploaded STL file to {result_stl_path}")

    except Exception as e:
        # エラーのスタックトレースを詳細に記録する
        error_trace = traceback.format_exc()
        print(f"Error processing {upload_id}: {e}\n{error_trace}")
        # エラー情報を保存することも可能
        error_blob = bucket.blob(f"results/{upload_id}/_ERROR")
        error_blob.upload_from_string(f"Error: {e}\n\nTraceback:\n{error_trace}")
    finally:
        # --- クリーンアップ ---
        flag_blob.delete()
        print(f"Deleted processing flag for {upload_id}")