# Palette HF Space: nginx reverse proxy to the Palette app on Code Engine.
FROM nginx:1.27-alpine

RUN apk add --no-cache openssl gettext

# Hugging Face runs Space containers as uid 1000, not root: everything nginx
# writes (pid, temp files, rendered config, htpasswd) lives under /tmp.
COPY nginx.conf.template /etc/nginx/nginx.conf.template
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && chmod -R a+rwX /var/cache/nginx /tmp

# Override per Space with a variable of the same name.
ENV UPSTREAM_URL=https://palette.1gxwxi8kos9y.us-east.codeengine.appdomain.cloud

USER 1000
EXPOSE 7860
ENTRYPOINT ["/entrypoint.sh"]
