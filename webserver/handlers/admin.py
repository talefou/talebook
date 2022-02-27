#!/usr/bin/env python3
# -*- coding: UTF-8 -*-

import datetime
import hashlib
import os
import logging
import re
import shutil
import subprocess
import ssl
import tempfile
import traceback

from gettext import gettext as _

import tornado
from webserver import loader
from webserver.handlers.base import BaseHandler, auth, js
from webserver.models import Reader

CONF = loader.get_settings()


class AdminUsers(BaseHandler):
    @js
    @auth
    def get(self):
        if not self.admin_user:
            return {"err": "permission.not_admin", "msg": _(u"当前用户非管理员")}

        num = max(10, int(self.get_argument("num", 20)))
        page = max(0, int(self.get_argument("page", 1)) - 1)
        sort = self.get_argument("sort", "access_time")
        desc = self.get_argument("desc", "desc")
        logging.debug("num=%d, page=%d, sort=%s, desc=%s" % (num, page, sort, desc))

        f = {
            "id": Reader.id,
            "access_time": Reader.access_time,
            "create_time": Reader.create_time,
            "update_time": Reader.update_time,
            "username": Reader.username,
        }.get(sort, Reader.id)
        if desc == "false":
            f = f.asc()
        else:
            f = f.desc()

        query = self.session.query(Reader).order_by(f)
        total = query.count()
        start = page * num
        items = []
        for user in query.limit(num).offset(start).all():
            d = {
                "id": user.id,
                "username": user.username,
                "name": user.name,
                "email": user.email,
                "avatar": user.avatar,
                "is_active": user.is_active(),
                "is_admin": user.is_admin(),
                "extra": dict(user.extra),
                "provider": user.social_auth[0].provider
                if hasattr(user, "social_auth") and user.social_auth.count()
                else "register",
                "create_time": user.create_time.strftime("%Y-%m-%d %H:%M:%S") if user.create_time else "N/A",
                "update_time": user.update_time.strftime("%Y-%m-%d %H:%M:%S") if user.update_time else "N/A",
                "access_time": user.access_time.strftime("%Y-%m-%d %H:%M:%S") if user.access_time else "N/A",
            }
            for attr in dir(user):
                if attr.startswith("can_"):
                    d[attr] = getattr(user, attr)()
            items.append(d)
        return {"err": "ok", "users": {"items": items, "total": total}}

    @js
    @auth
    def post(self):
        if not self.admin_user:
            return {"err": "permission.not_admin", "msg": _(u"当前用户非管理员")}
        data = tornado.escape.json_decode(self.request.body)
        uid = data.get("id", None)
        if not uid:
            return {"err": "params.invalid", "msg": _(u"参数错误")}
        del data["id"]
        if not data:
            return {"err": "params.fields.invalid", "msg": _(u"用户配置项参数错误")}
        user = self.session.query(Reader).filter(Reader.id == uid).first()
        if not user:
            return {"err": "params.user.not_exist", "msg": _(u"用户ID错误")}
        if "active" in data:
            user.active = data["active"]
        if "admin" in data:
            user.admin = data["admin"]

        p = data.get("permission", "")
        if not isinstance(p, str):
            return {"err": "params.permission.invalid", "msg": _(u"权限参数不对")}
        if p:
            user.set_permission(p)
        user.save()
        return {"err": "ok"}


class AdminTestMail(BaseHandler):
    @js
    @auth
    def post(self):
        mail_server = self.get_argument("smtp_server")
        mail_username = self.get_argument("smtp_username")
        mail_password = self.get_argument("smtp_password")

        mail_from = mail_username
        mail_to = mail_username
        mail_subject = _(u"Calibre功能验证邮件")
        mail_body = _(u"这是一封测试邮件，验证邮件参数是否配置正确。")

        try:
            self.mail(
                mail_from,
                mail_to,
                mail_subject,
                mail_body,
                relay=mail_server,
                username=mail_username,
                password=mail_password,
            )
            return {"err": "ok", "msg": _(u"发送成功")}
        except Exception as e:
            logging.error(traceback.format_exc())
            return {"err": "email.server_error", "msg": str(e)}


class AdminOwnerMode(BaseHandler):
    @auth
    def get(self):
        user_id = self.get_argument("user_id", None)
        if user_id and self.is_admin():
            self.set_secure_cookie("admin_id", self.user_id())
            self.set_secure_cookie("user_id", user_id)
        self.redirect("/", 302)


class SettingHandler(BaseHandler):
    def save_extra_settings(self, args):
        if args != CONF:
            CONF.update(args)

        # update index.html
        html = self.render_string("index.html", **CONF)
        html.replace("Calibre Webserver", CONF["site_title"])
        page = os.path.join(CONF["html_path"], "index.html")
        try:
            with open(page, "w") as f:
                f.write(html)
        except:
            logging.error(traceback.format_exc())
            return {"err": "file.permission", "msg": _(u"更新index.html失败！请确保文件的权限为可写入！")}

        # don't update running environment for now
        args["installed"] = True
        try:
            args.dumpfile()
        except:
            logging.error(traceback.format_exc())
            return {"err": "file.permission", "msg": _(u"更新磁盘配置文件失败！请确保配置文件的权限为可写入！")}

        # ok, it's safe to update current environment
        CONF["installed"] = True
        return {"err": "ok", "rsp": args}


class AdminSettings(SettingHandler):
    @js
    @auth
    def get(self):
        if not self.admin_user:
            return {"err": "permission", "msg": _(u"无权访问此接口")}

        site_url = self.request.protocol + "://" + self.request.host
        sns = [
            {"value": "qq", "text": "QQ", "link": "https://connect.qq.com/"},
            {
                "value": "amazon",
                "text": "Amazon",
                "link": "https://developer.amazon.com/zh/docs/login-with-amazon/web-docs.html",
            },
            {
                "value": "github",
                "text": "Github",
                "link": "https://github.com/settings/applications/new",
            },
            {
                "value": "weibo",
                "text": u"微博",
                "link": "http://open.weibo.com/developers",
            },
            {
                "value": "wechat",
                "text": u"微信",
                "link": "https://developers.weixin.qq.com/doc/offiaccount/OA_Web_Apps/Wechat_webpage_authorization.html",
            },
        ]
        return {"err": "ok", "settings": CONF, "sns": sns, "site_url": site_url}

    @js
    @auth
    def post(self):
        data = tornado.escape.json_decode(self.request.body)
        KEYS = [
            "ALLOW_GUEST_DOWNLOAD",
            "ALLOW_GUEST_PUSH",
            "ALLOW_GUEST_READ",
            "ALLOW_REGISTER",
            "BOOK_NAMES_FORMAT",
            "FRIENDS",
            "FOOTER",
            "INVITE_CODE",
            "INVITE_MESSAGE",
            "INVITE_MODE",
            "MAX_UPLOAD_SIZE",
            "RESET_MAIL_CONTENT",
            "RESET_MAIL_TITLE",
            "SIGNUP_MAIL_CONTENT",
            "SIGNUP_MAIL_TITLE",
            "SOCIALS",
            "autoreload",
            "cookie_secret",
            "douban_apikey",
            "douban_baseurl",
            "douban_max_count",
            "site_title",
            "smtp_password",
            "smtp_server",
            "smtp_username",
            "static_host",
            "xsrf_cookies",
            "settings_path",
            "avatar_service",
        ]

        args = loader.SettingsLoader()
        args.clear()

        for key, val in data.items():
            if key.startswith("SOCIAL_AUTH"):
                if key.endswith("_KEY") or key.endswith("_SECRET"):
                    args[key] = val
            elif key in KEYS:
                args[key] = val

        return self.save_extra_settings(args)


class AdminInstall(SettingHandler):
    def should_be_invited(self):
        pass

    def should_be_installed(self):
        pass

    @js
    def get(self):
        err = "installed" if CONF.get("installed", True) else "not_intalled"
        return {"err": err}

    @js
    def post(self):
        if CONF.get("installed", True):
            return {"err": "installed", "msg": _(u"不可重复执行安装操作")}

        code = self.get_argument("code", "").strip()
        email = self.get_argument("email", "").strip().lower()
        title = self.get_argument("title", "").strip()
        invite = self.get_argument("invite", "").strip()
        username = self.get_argument("username", "").strip().lower()
        password = self.get_argument("password", "").strip()
        if not username or not password or not email or not title:
            return {"err": "params.invalid", "msg": _(u"填写的内容有误")}
        if not re.match(Reader.RE_EMAIL, email):
            return {"err": "params.email.invalid", "msg": _(u"Email无效")}
        if len(username) < 5 or len(username) > 20 or not re.match(Reader.RE_USERNAME, username):
            return {"err": "params.username.invalid", "msg": _(u"用户名无效")}
        if len(password) < 8 or len(password) > 20 or not re.match(Reader.RE_PASSWORD, password):
            return {"err": "params.password.invalid", "msg": _(u"密码无效")}

        # 避免重复创建
        user = self.session.query(Reader).filter(Reader.username == username).first()
        if not user:
            user = Reader()
            user.username = username
            user.name = username
            user.email = email
            user.avatar = CONF["avatar_service"] + "/avatar/" + hashlib.md5(email.encode("UTF-8")).hexdigest()
            user.create_time = datetime.datetime.now()
            user.update_time = datetime.datetime.now()
            user.access_time = datetime.datetime.now()
            user.active = True
            user.admin = True
            user.extra = {"kindle_email": ""}
            user.set_secure_password(password)
            try:
                user.save()
            except:
                import traceback

                logging.error(traceback.format_exc())
                return {"err": "db.error", "msg": _(u"系统异常，请重试或更换注册信息")}

        args = loader.SettingsLoader()
        args.clear()

        import uuid

        # set a random secret
        args["cookie_secret"] = u"%s" % uuid.uuid1()
        args["site_title"] = title
        if invite == "true":
            args["INVITE_MODE"] = True
            args["INVITE_CODE"] = code
        else:
            args["INVITE_MODE"] = False
        return self.save_extra_settings(args)


class SSLHandlerLogic:
    def check_ssl_chain(self, crt_body, key_body):
        """return None if ok, else Err"""
        with tempfile.NamedTemporaryFile() as crt_file, tempfile.NamedTemporaryFile() as key_file:
            crt_file.write(crt_body)
            key_file.write(key_body)
            crt_file.flush()
            key_file.flush()
            return self.check_ssl_chain_files(crt_file.name, key_file.name)

    def check_ssl_chain_files(self, crt_file, key_file):
        ctx = ssl.SSLContext()
        try:
            ctx.load_cert_chain(crt_file, key_file)
        except ssl.SSLError as err:
            return err
        return None

    def save_files(self, crt_body, key_body):
        with open(CONF["ssl_crt_file"], "w+b") as f:
            f.write(crt_body)

        with open(CONF["ssl_key_file"], "w+b") as f:
            f.write(key_body)

    def nginx_check(self):
        return subprocess.run(["nginx", "-t"], check=True)

    def nginx_reload(self):
        return subprocess.run(["service", "nginx", "reload"], check=True)

    def run(self, ssl_crt, ssl_key):
        err = self.check_ssl_chain(ssl_crt, ssl_key)
        if err is not None:
            return {"err": "params.ssl_error", "msg": _(u"证书或密钥校验失败: %s" % err)}

        try:
            self.save_files(ssl_crt, ssl_key)
        except Exception as err:
            import traceback

            logging.error(traceback.format_exc())
            return {"err": "internal.ssl_save_error", "msg": _(u"证书存储失败: %s" % err)}

        # testing nginx config
        try:
            self.nginx_check()
        except subprocess.CalledProcessError as err:
            return {"err": "internal.nginx_test_error", "msg": _(u"NGINX配置异常: %s") % err}

        # reload nginx config
        try:
            self.nginx_reload()
        except subprocess.CalledProcessError as err:
            return {"err": "internal.nginx_reload_error", "msg": _(u"NGINX重新加载配置异常: %s") % err}

        return {"err": "ok"}


class AdminUploadSSL(BaseHandler):
    def get_upload_file(self):
        # for unittest mock
        ssl_crt = self.request.files["ssl_crt"][0]
        ssl_key = self.request.files["ssl_key"][0]
        return (ssl_crt["body"], ssl_key["body"])

    # TODO:
    #   - add GET interface to show the hostname/outdate of certifacates

    @js
    @auth
    def post(self):
        logic = SSLHandlerLogic()

        logging.error("got request")
        if not self.is_admin():
            return {"err": "permission", "msg": _(u"无权操作")}

        ssl_crt, ssl_key = self.get_upload_file()
        return logic.run(ssl_crt, ssl_key)


def routes():
    return [
        (r"/api/admin/ssl", AdminUploadSSL),
        (r"/api/admin/users", AdminUsers),
        (r"/api/admin/install", AdminInstall),
        (r"/api/admin/settings", AdminSettings),
        (r"/api/admin/testmail", AdminTestMail),
    ]
