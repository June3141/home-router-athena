# home-router-athena

Debian ベースのソフトウェアルーター構成を Ansible で IaC 化する実験プロジェクト。
本番ハード（AIOPCWA AI401）導入前の検証用。

## 構成

```
ansible/
├── ansible.cfg
├── inventory/hosts.yml          # 実験機の接続情報
├── playbooks/
│   ├── bootstrap.yml            # Phase 0: 初期 user / SSH 鍵 / sudo (1 回のみ手動)
│   ├── site.yml                 # 全 Phase エントリポイント
│   ├── network.yml              # Phase 1: systemd-networkd / nftables / dnsmasq
│   ├── dns.yml                  # Phase 2: Unbound / AdGuard Home
│   └── netbird.yml              # Phase 3: Netbird Subnet Router
├── roles/
│   ├── bootstrap/               # Phase 0 実装
│   ├── network/                 # Phase 1 実装
│   ├── unbound/                 # Phase 2 実装
│   ├── adguard/                 # Phase 2 実装
│   └── netbird/                 # Phase 3 実装
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
# 1. ホスト固有の値（IP / SSH ユーザー / NIC 名 / LAN セグメント / 公開鍵）を設定
cp ansible/host_vars/athena-lab.yml.example ansible/host_vars/athena-lab.yml
$EDITOR ansible/host_vars/athena-lab.yml
# inventory/hosts.yml には構造（どのグループにどのホストがいるか）だけが入る

# 2. secrets を作成（Phase 3 以降で必要）
#    Phase 3 では netbird_setup_key が必要。Netbird coordination
#    server (Cloud or self-hosted) の管理画面で setup key を発行し、
#    secrets.yml に書き込んでから vault 暗号化する。
#    self-host する場合は host_vars 等で netbird_management_url も上書き。
cp ansible/vars/secrets.yml.example ansible/vars/secrets.yml
$EDITOR ansible/vars/secrets.yml
ansible-vault encrypt ansible/vars/secrets.yml

# 3. (新規ホストのみ 1 回) Phase 0 bootstrap
#    OS インストール直後の root SSH (password) から、管理ユーザー /
#    鍵 / sudo を整備する。完了後 root SSH ログインは自動で無効化される。
ansible-playbook \
  -i ansible/inventory/hosts.yml \
  -e ansible_user=root --ask-pass \
  ansible/playbooks/bootstrap.yml

# 4. 疎通確認 (Phase 0 で作った管理ユーザー経由)
cd ansible
ansible routers -m ping

# 5. 全 Phase 適用（site.yml = network → dns → netbird の順）
ansible-playbook playbooks/site.yml --ask-vault-pass
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
# 全 Phase (site.yml が import_playbook している範囲)
# Phase 3 が含まれるので vault password 入力が必要
ansible-playbook playbooks/site.yml --ask-vault-pass

# Phase 単位
ansible-playbook playbooks/network.yml                          # Phase 1
ansible-playbook playbooks/dns.yml                              # Phase 2
ansible-playbook playbooks/netbird.yml --ask-vault-pass         # Phase 3

# 特定タグだけ
ansible-playbook playbooks/network.yml --tags nftables
ansible-playbook playbooks/dns.yml --tags adguard
```

## 注意

- **Phase 0 (bootstrap) は新規 OS 直後の 1 回のみ**実行する想定。完了時点で
  root の SSH ログインが無効化されるので、再実行時は管理ユーザーで接続する。
  bootstrap_ssh_pubkey が誤った鍵だとロックアウトするので、適用前にコンソール
  アクセス手段を確保すること。
- `network` ロールは systemd-networkd / nftables / dnsmasq を有効化する。
  既存の NetworkManager や ifupdown を使っている機体ではセッションが切れる可能性があるため、
  リモート機への適用前にコンソールアクセス手段を確保しておくこと。
- **Phase 3 (Netbird) の前提**: coordination server (Netbird Cloud or
  self-hosted) が稼働済みで、router 用の setup key が発行されていること。
  setup key は vault 暗号化した `vars/secrets.yml` に `netbird_setup_key:`
  として置く。self-host なら `host_vars/<host>.yml` で
  `netbird_management_url` も上書き。
- MAP-E 設定（JPNE v6プラス）は本番環境向けで未実装。
- Phase 完了時点でタグを打つ運用 (`git tag phase-1` 等)。
