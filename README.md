# DICOM to STL Web Tool (DICOM → STL 変換ツール)

## 概要

このプロジェクトは、医療用画像フォーマットであるDICOMファイルを3Dプリンターなどで利用可能なSTL（Stereolithography）ファイルに変換するためのWebアプリケーションです。
ユーザーはブラウザ上でDICOMファイルをアップロードし、3Dモデルをプレビューした後、STLファイルとしてダウンロードすることができます。

## 主な機能

*   **DICOMファイルのアップロード**: 単一または複数のDICOMスライスファイルをアップロードできます。
*   **3Dモデルのプレビュー**: アップロードされたDICOMファイルから再構成された3Dモデルをインタラクティブに表示します。
*   **STLへの変換とダウンロード**: 3DモデルをSTL形式に変換し、ダウンロードする機能を提供します。

## 技術スタック

このプロジェクトで使用されている主要な技術は以下の通りです。（※プロジェクトに合わせて内容を更新してください）

*   **フロントエンド**: HTML5, CSS3, JavaScript
*   **バックエンド**: Firebase (Cloud Functions, Cloud Storage)
*   **DICOM処理**: pydicom, scikit-image, numpy
*   **3Dメッシュ生成**: scikit-image (marching cubes)

## セットアップ方法

フロントエンドとFirebaseエミュレータをローカル環境で動かすには、以下の手順に従ってください。

1.  **リポジトリをクローンします:**
    ```bash
    git clone https://github.com/tfuru/dicom2stl.git 
    cd dicom2stl
    ```

2.  **Firebase CLIをインストールし、ログインします:**
    (未インストールの場合は [公式ドキュメント](https://firebase.google.com/docs/cli) を参照してください)
    ```bash
    firebase login
    ```

3.  **ローカルエミュレータを起動します:**
    ```bash
    firebase emulators:start
    ```
    起動後、Hostingのエミュレータが実行されているURL (例: `http://127.0.0.1:5000`) にブラウザでアクセスしてください。

## 使い方

1.  アプリケーションにアクセスします。
2.  「ファイルを選択」ボタンを使い、変換したいDICOMファイル（群）をアップロードします。
3.  処理が完了すると、3Dモデルのプレビューが表示されます。
4.  「STLをダウンロード」ボタンをクリックして、生成されたSTLファイルを保存します。


## 開発環境
開発のために firebase 環境を整える手順  
```
npm install -g firebase-tools
firebase login

git clone https://github.com/tfuru/dicom2stl.git 
cd dicom2stl

brew install pyenv
cd functions
pyenv local 3.12.4
python3.12 -m venv venv
. venv/bin/activate
pip install -r requirements.txt
cd ..

firebase deploy --only functions
firebase deploy
```

## ライセンス

このプロジェクトは MIT License のもとで公開されています。