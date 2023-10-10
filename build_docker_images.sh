#!/bin/bash

[ -d palace_circulation_admin_ui ] && rm -rf palace_circulation_admin_ui

git clone --branch=main ssh://git.lingsoft.fi/home/git/palace_circulation_admin_ui.git

docker compose --progress=plain build $* 

[ -d palace_circulation_admin_ui ] && rm -rf palace_circulation_admin_ui


