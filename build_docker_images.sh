#!/bin/bash

if [ "$1" = "admin" ]
then
  [ -d palace_circulation_admin_ui ] && rm -rf palace_circulation_admin_ui
  git clone --branch=main ssh://git.lingsoft.fi/home/git/palace_circulation_admin_ui.git
  docker compose --progress=plain build admin
  [ -d palace_circulation_admin_ui ] && rm -rf palace_circulation_admin_ui
  docker create --name temp-admin "${PWD##*/}"-admin:latest
  docker cp temp-admin:/mnt/tarball/admin_dist.tar.gz docker/admin_dist.tar.gz
  docker rm temp-admin
  docker image rm "${PWD##*/}"-admin:latest
elif [ "$#" -eq 1 ]
then
  docker compose --progress=plain build $*
else
  ./build_docker_images.sh admin
  docker compose --progress=plain build webapp scripts pg minio os
fi



