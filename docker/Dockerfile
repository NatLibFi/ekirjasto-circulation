###############################################################################
## lcpencrypt
###############################################################################

FROM golang:1.17 AS builder

ENV TZ=Europe/Helsinki

LABEL maintainer="The Palace Project <info@thepalaceproject.org>"

RUN go get -v github.com/readium/readium-lcp-server/lcpencrypt && \
    echo Europe/Helsinki >/etc/timezone && \
    ln -sf /usr/share/zoneinfo/Europe/Helsinki /etc/localtime && \
    dpkg-reconfigure -f noninteractive tzdata

###############################################################################
## Base Image
###############################################################################

FROM phusion/baseimage:focal-1.1.0 As base

ENV TZ=Europe/Helsinki

COPY --from=builder /go/bin/lcpencrypt /go/bin/lcpencrypt

# Copy over all Palace build files for this image
COPY ./docker /ls_build
RUN /bin/bash -c "/ls_build/system_setup.sh"
COPY --chown=1000:1000 poetry.lock pyproject.toml /var/www/circulation/
RUN /bin/bash -c "/ls_build/app_setup.sh \
      && /ls_build/logrotate.sh"

COPY --chown=1000:1000 . /var/www/circulation

###############################################################################
## Circ-exec Image
###############################################################################

FROM base as exec

ENV TZ=Europe/Helsinki
ENV SIMPLIFIED_DB_TASK "ignore"
ENV SIMPLIFIED_SCRIPT_NAME ""

RUN /bin/bash -c "rm -rf /ls_build && /bd_build/cleanup.sh"

VOLUME /var/log
WORKDIR /home/simplified/circulation/bin
CMD ["/sbin/my_init", "--skip-runit", "--quiet", "--", \
     "/bin/bash", "-c", \
     "source ../env/bin/activate && ./${SIMPLIFIED_SCRIPT_NAME}"]

###############################################################################
## Circ-scripts Image
###############################################################################

FROM base as scripts

ENV SIMPLIFIED_DB_TASK "auto"
# Set the local timezone in /docker/simplified_cron.sh
ENV TZ=Europe/Helsinki

RUN /bin/bash -c "/ls_build/simplified_cron.sh \
      && rm -rf /ls_build && /bd_build/cleanup.sh"

VOLUME /var/log
WORKDIR /home/simplified/circulation/bin

CMD ["/sbin/my_init"]

###############################################################################
## Circ-webapp Image
###############################################################################

FROM base as webapp
ENV TZ=Europe/Helsinki

ENV SIMPLIFIED_DB_TASK "ignore"

COPY docker/admin_dist.tar.gz /mnt/admin_dist.tar.gz

RUN /bin/bash -c "/ls_build/nginx.sh && \
      /ls_build/uwsgi.sh && \
      rm -rf /ls_build && /bd_build/cleanup.sh && \
	  apt-get install -y --no-install-recommends tar && \
	  cd /var/www/circulation/api/admin && \
	  tar xf /mnt/admin_dist.tar.gz && \
	  chown -R simplified:simplified dist && \
	  rm /mnt/admin_dist.tar.gz "

COPY --chown=simplified:simplified docker/config.py_admin /var/www/circulation/api/admin/config.py

VOLUME /var/log
WORKDIR /home/simplified/circulation
EXPOSE 80

CMD ["/sbin/my_init"]
