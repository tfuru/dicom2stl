import os
import tempfile
import pydicom
import traceback
import numpy as np
from skimage import measure
from stl import mesh

from firebase_admin import initialize_app, storage as admin_storage
from firebase_functions import storage_fn
from firebase_functions.options import set_global_options
from google.cloud import storage

# DICOMのような重い処理を扱うため、メモリとタイムアウト時間を増やします
# リージョンはご自身の環境に合わせて変更してください
set_global_options(region="asia-northeast1", memory="1GiB", timeout_sec=540)

initialize_app()


@storage_fn.on_object_finalized()
def on_dicom_upload(event: storage_fn.CloudEvent) -> None:
    """
    Cloud StorageにファイルがアップロードされたときにSTL変換処理を起動するトリガー。
    """
    bucket_name = event.data["bucket"]
    file_path = event.data["name"]

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
    if len(path_parts) < 3:
        print(f"Invalid file path structure: {file_path}")
        return
    upload_id = path_parts[1]
    dicom_dir_prefix = f"uploads/{upload_id}/"
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
            flag_blob.delete()
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