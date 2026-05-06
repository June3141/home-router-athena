# home-router-athena

Debian ベースのソフトウェアルーター構成を Ansible で IaC 化する実験プロジェクト。
本番ハード（AIOPCWA AI401）導入前の検証用。

## 構成

```
ansible/
├── ansible.cfg
├── inventory/hosts.yml          # 実験機の接続情報
├── playbooks/
│   ├── site.yml                 # 全 Phase エントリポイント
│   ├── network.yml              # Phase 1: systemd-networkd / nftables / dnsmasq
│   ├── dns.yml                  # Phase 2: Unbound / AdGuard Home
│   ├── netbird.yml              # Phase 3: Netbird Subnet Router
│   └── monitoring.yml           # Phase 4: node_exporter
├── roles/
│   ├── network/                 # Phase 1 実装
│   ├── unbound/                 # Phase 2 雛形
│   ├── adguard/                 # Phase 2 雛形
│   ├── netbird/                 # Phase 3 雛形
│   └── node_exporter/           # Phase 4 雛形
└── vars/
    ├── common.yml
    └── secrets.yml.example      # 実体は ansible-vault で暗号化
```

## 前提

- Ansible 2.15+ / Python 3.10+
- ターゲットは Debian 12 以降を想定
- SSH 鍵認証で `ansible_user` に接続でき、sudo がパスワード無しで通ること

## セットアップ

```sh
# 1. ホスト固有の値（IP / SSH ユーザー / NIC 名 / LAN セグメント）を設定
cp ansible/host_vars/athena-lab.yml.example ansible/host_vars/athena-lab.yml
$EDITOR ansible/host_vars/athena-lab.yml
# inventory/hosts.yml には構造（どのグループにどのホストがいるか）だけが入る

# 2. secrets を作成（Phase 3 以降で必要）
cp ansible/vars/secrets.yml.example ansible/vars/secrets.yml
ansible-vault encrypt ansible/vars/secrets.yml

# 3. 疎通確認
cd ansible
ansible routers -m ping

# 4. Phase 1 適用
ansible-playbook playbooks/network.yml
```

## ローカルチェック

CI と同じ check を Taskfile 経由でローカル実行する。
Python 系ツール (yamllint / ansible-lint / pre-commit) は uv 管理の `.venv` に入る。
Betterleaks は Go バイナリなので $PATH に置く。

```sh
# 1. ホスト側に必要なもの
#    - uv:        https://docs.astral.sh/uv/getting-started/installation/
#    - task:      https://taskfile.dev/installation/
#    - betterleaks: https://github.com/betterleaks/betterleaks/releases
#                  からバイナリを取得して ~/.local/bin/ などに設置

# 2. プロジェクト venv 作成 + dev tools install
task setup            # = uv sync

# 3. pre-commit + pre-push hook を有効化（一度だけ）
#    pre-commit: 各 commit で staged 差分を Betterleaks にかける
#    pre-push:   push 直前に working tree + git history 全体をスキャン
task install:hooks

# 4. 全チェック（GHA と同じ内容）
task check

# 個別
task lint:yaml
task lint:ansible
task scan:secrets        # working tree + git history を Betterleaks で走査
```

push / PR 時は `.github/workflows/check.yml` が同じ `task check` を回す。

## 実行例

```sh
# 全 Phase（site.yml が import_playbook している範囲）
ansible-playbook playbooks/site.yml

# Phase 1 のみ
ansible-playbook playbooks/network.yml

# 特定タグだけ
ansible-playbook playbooks/network.yml --tags nftables
```

## 注意

- `network` ロールは systemd-networkd / nftables / dnsmasq を有効化する。
  既存の NetworkManager や ifupdown を使っている機体ではセッションが切れる可能性があるため、
  リモート機への適用前にコンソールアクセス手段を確保しておくこと。
- MAP-E 設定（andline / JPNE v6プラス）は本番環境向けで未実装。
- Phase 完了時点でタグを打つ運用 (`git tag phase-1` 等)。
