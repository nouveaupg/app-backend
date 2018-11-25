from flask import (
    Blueprint, render_template, request, url_for, redirect, current_app
)
from werkzeug.exceptions import abort
import database
import json
from hashlib import sha256
from users import UserContext
from events import Event
from transaction_pricing import BlockInfo
import datetime
from ledger import TransactionLedger
from credits import Credits
from smart_contract import SmartContract
import re

TOKEN_NAME_REGEX = re.compile("^[A-Za-z0-9]{4,36}$")
TOKEN_SYMBOL_REGEX = re.compile("^[A-Z0-9]{1,5}$")
TOKEN_COUNT_REGEX = re.compile("^[0-9]{1,16}$")

admin_blueprint = Blueprint('admin', __name__, url_prefix="/admin")

PAGE_LIMIT = 20
MOVING_AVERAGE_WINDOW = 50


@admin_blueprint.route('/')
def admin_no_session():
    return render_template("admin/admin_login.jinja2")


@admin_blueprint.route('/<session_token>/transactions')
def admin_main_transactions(session_token):
    return admin_main(session_token, transactions=True)


@admin_blueprint.route('/<session_token>')
def admin_main(session_token, transactions=False):
    db = database.Database(logger=current_app.logger)
    user_id = db.validate_session(session_token)
    if user_id:
        user_ctx = UserContext(user_id, db, current_app.logger)
        launch_ico = user_ctx.check_acl("launch-ico")
        onboard_users = user_ctx.check_acl("onboard-users")
        reset_passwords = user_ctx.check_acl("reset-passwords")
        ethereum_network = user_ctx.check_acl("ethereum-network")
        view_event_log = user_ctx.check_acl("view-event-log")
        issue_credits = user_ctx.check_acl("issue-credits")
        manager = len(user_ctx.acl()["administrator"]) > 0 or len(user_ctx.get_manager_tokens()) > 0
        if user_ctx.user_info["email_address"] == "admin":
            manager = True
        block_info = BlockInfo(db, logger=current_app.logger)
        metrics = block_info.calculate_main_graphs()

        graphing_metrics = {
            "moving_average": {"gas_price": json.dumps(metrics["moving_average"]["gas_price"])},
                "London": {
                    "gas_price": json.dumps(metrics["London"]),
                },
                "Amsterdam": {
                    "gas_price": json.dumps(metrics["Amsterdam"]),
                },
                "Dallas": {
                    "gas_price": json.dumps(metrics["Dallas"])
                }
        }

        return render_template("admin/admin_main.jinja2",
                               session_token=session_token,
                               launch_ico=launch_ico,
                               onboard_users=onboard_users,
                               reset_passwords=reset_passwords,
                               ethereum_network=ethereum_network,
                               view_event_log=view_event_log,
                               issue_credits=issue_credits,
                               manager=manager,
                               metrics=graphing_metrics)
    else:
        return render_template("admin/admin_login.jinja2", error="Invalid session.")


@admin_blueprint.route('/users/reset-password/<user_id>/<session_token>')
def reset_password(user_id, session_token):
    user_id = int(user_id)
    if user_id < 1:
        raise ValueError
    db = database.Database()
    auth_user_id = db.validate_session(session_token)
    if auth_user_id:
        auth_user_ctx = UserContext(auth_user_id, db, current_app.logger)
        if auth_user_ctx.check_acl("reset-passwords"):
            user_info = db.get_user_info(user_id)
            return render_template("admin/admin_confirmation.jinja2",
                                   confirmation_type="reset-password",
                                   confirmation_value=user_id,
                                   title="Reset Password",
                                   confirmation_title="Reset Password",
                                   confirmation_message="Reset password for <span class=\"sky_blue\">{0}</span>?".format(
                                       user_info["email_address"]),
                                   new_password=True,
                                   choices=["Cancel"],
                                   default_choice="Reset Password",
                                   session_token=session_token)
    abort(403)


@admin_blueprint.route('/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == "POST":
        username = request.form['username']
        password = request.form['password']
        db = database.Database(logger=current_app.logger)
        session_data = db.login(username, password, request.access_route[-1])
        if session_data:
            return redirect(url_for("admin.admin_main", session_token=session_data[1]))
        else:
            return render_template("admin/admin_login.jinja2",
                                   error="Invalid email/password combination.")
    return render_template("admin/admin_login.jinja2")


@admin_blueprint.route('/users/recover_account')
def view_account_recovery():
    confirmation_message = "If your e-mail is in our database, you will receive an e-mail with "
    confirmation_message += "instructions on resetting your password"
    return render_template("admin/admin_confirmation.jinja2",
                           email_address=True,
                           title="Recover Account",
                           confirmation_type="recover_email",
                           confirmation_title="Recover your account",
                           confirmation_message=confirmation_message,
                           default_choice="Send E-mail",
                           choices=["Cancel"])


@admin_blueprint.route('/event_log/<session_token>')
def view_event_log(session_token):
    db = database.Database(logger=current_app.logger)
    user_id = db.validate_session(session_token)
    if user_id:
        authorized = db.validate_permission(user_id, "view-event-log")
        if authorized:
            event_types = db.list_event_types()
            return render_template("admin/admin_view_event_log.jinja2",
                                   session_token=session_token,
                                   event_types=event_types)
    abort(403)


@admin_blueprint.route('/event_log/filter', methods=['GET', 'POST'])
def filter_event_log():
    if request.method == "POST":
        session_token = request.form['session_token']
        output_format = request.form['output_format']
        event_limit = int(request.form['event_limit'])
        event_filters = request.form.getlist('event_filter')

        db = database.Database(logger=current_app.logger)
        user_id = db.validate_session(session_token)
        if user_id:
            authorized = db.validate_permission(user_id, "view-event-log")
            if authorized:
                event_types = db.list_event_types()
                events = []
                for each in event_filters:
                    event_type_id = int(each)
                    event_type_name = None
                    for every_type in event_types:
                        if every_type["event_type_id"] == event_type_id:
                            event_type_name = every_type["event_type_name"]
                            break
                    events_of_type = db.get_latest_events(event_type_id, event_limit)
                    for each_event in events_of_type:
                        new_event_obj = dict(each_event)
                        new_event_obj["event_type"] = event_type_name
                        events.append(new_event_obj)

                events = sorted(events, key=lambda event: event['event_id'])
                events.reverse()
                for every_event in events:
                    every_event['event_created'] = every_event['event_created'].isoformat()
                events = events[:event_limit]
                json_data = None
                if output_format == "json":
                    json_data = json.dumps(events)
                csv_data = None
                if output_format == "csv" and len(events) > 0:
                    event_keys = events[0].keys()
                    csv_data = ""
                    for every_key in event_keys:
                        csv_data += every_key + ","
                    csv_data = csv_data[:len(csv_data) - 1] + "\n"
                    for every_event in events:
                        event_row = ""
                        for every_key in event_keys:
                            event_row += str(every_event[every_key]) + ","
                        event_row += event_row[:len(event_row) - 1] + "\n"
                        csv_data += event_row
                html_data = None
                if output_format == "html":
                    html_data = events
                return render_template("admin/admin_view_event_log.jinja2",
                                       session_token=session_token,
                                       event_types=event_types,
                                       json_data=json_data,
                                       csv_data=csv_data,
                                       html_data=html_data,
                                       event_filters=event_filters)
        else:
            abort(403)
    abort(500)


@admin_blueprint.route('/eth_network/<session_token>')
def eth_network_admin(session_token):
    db = database.Database(logger=current_app.logger)
    user_id = db.validate_session(session_token)
    if user_id:
        authorized = db.validate_permission(user_id, "ethereum-network")
        if authorized:
            peer_data = {}
            update_node_event_type = Event("Ethereum Node Update", db, logger=current_app.logger)
            epoch = datetime.datetime.today() - datetime.timedelta(hours=24)
            nodes = db.list_ethereum_nodes()
            updates = update_node_event_type.get_latest_events(100)
            for each_node in nodes:
                peer_data[each_node["node_identifier"]] = []
                node_id = each_node["id"]
                for each_update in updates:
                    if each_update[2] == node_id:
                        event_data = json.loads(each_update[0])
                        if event_data["synchronized"]:
                            peer_count = event_data["peers"]
                        else:
                            blocks_behind = {"count": event_data['blocks_behind'],
                                             "node_id": node_id}
            peer_strings = {}

            for key in peer_data.keys():
                peer_strings[key] = str(peer_data[key])

            return render_template("admin/admin_eth_network.jinja2",
                                   session_token=session_token,
                                   eth_nodes=nodes,
                                   peer_data=peer_data,
                                   blocks_behind=blocks_behind)
    abort(403)


@admin_blueprint.route('/users/create/<session_token>')
def admin_create_user(session_token):
    db = database.Database(current_app.logger)
    user_id = db.validate_session(session_token)
    if user_id:
        authorized = db.validate_permission(user_id, "onboard-users")
        if authorized:
            return render_template("admin/admin_create_user.jinja2",
                                   session_token=session_token)
    abort(403)


def get_owned_tokens(user_id, db, logger=None):
    erc20_publish_event = Event("ERC20 Token Created", db, logger)
    event_count = erc20_publish_event.get_event_count()
    # get all ERC20 publish events for now...
    publish_events = erc20_publish_event.get_latest_events(event_count)
    smart_contracts = db.get_smart_contracts(user_id)
    owned_tokens = []
    for each in smart_contracts:
        pending = False
        for every_event in publish_events:
            event_data = json.loads(every_event[0])
            if event_data["token_id"] == each["token_id"]:
                pending = True
        token_data = {"token_id": each["token_id"],
                      "token_name": each["token_name"],
                      "ico_tokens": each["tokens"],
                      "token_symbol": each["token_symbol"],
                      "eth_address": each["eth_address"],
                      "created": each["created"],
                      "published": each["published"],
                      "pending": pending,
                      "max_priority": each["max_priority"]}
        if not pending:
            sc = SmartContract(each["token_id"])
            token_data["issued_tokens"] = sc.get_issued_token_count()
            token_data["issued_not_confirmed"] = 0
            token_data["confirmed_not_assigned"] = 0
        owned_tokens.append(token_data)
    owned_tokens = sorted(owned_tokens, key=lambda token: token['created'], reverse=True)
    return owned_tokens


@admin_blueprint.route('/tokens/<session_token>')
def admin_tokens(session_token):
    if session_token:
        db = database.Database(logger=current_app.logger)
        user_id = db.validate_session(session_token)
        ctx = UserContext(user_id, db=db, logger=db.logger)
        can_launch_ico = ctx.check_acl("launch-ico")
        erc20_publish_event = Event("ERC20 Token Created", db, current_app.logger)
        event_count = erc20_publish_event.get_event_count()
        # get all ERC20 publish events for now...
        publish_events = erc20_publish_event.get_latest_events(event_count)
        if can_launch_ico or len(ctx.acl()["management"]) > 0:
            owned_tokens = []
            for key in ctx.acl()["management"].keys():
                token_id = ctx.acl()["management"][key]["token_id"]
                token_info = db.get_smart_contract_info(token_id)
                owned_tokens.append(token_info)
            owned_tokens.extend(get_owned_tokens(user_id, db, current_app.logger))
            if len(owned_tokens) == 0:
                owned_tokens = None
            email_address = ctx.user_info["email_address"]
            last_logged_in = ctx.user_info["last_logged_in"].isoformat()
            last_logged_in_ip = ctx.user_info["last_logged_in_ip"]
            credit_ctx = Credits(user_id, db, current_app.logger)
            credit_balance = credit_ctx.get_credit_balance()
            return render_template("admin/admin_tokens.jinja2",
                                   session_token=session_token,
                                   owned_tokens=owned_tokens,
                                   can_launch_ico=can_launch_ico,
                                   email_address=email_address,
                                   last_logged_in=last_logged_in,
                                   last_logged_in_ip=last_logged_in_ip,
                                   credit_balance=credit_balance)
    abort(403)


@admin_blueprint.route('/confirm', methods=["POST"])
def admin_confirm():
    session_token = request.form["session_token"]
    confirmation_type = request.form["confirmation_type"]
    confirmation_val = request.form["confirmation_value"]
    choice = request.form["choice"]
    if confirmation_type == "reset_email":
        return redirect(url_for('admin.admin_main', session_token=session_token))
    elif confirmation_type == "erc20_publish" and choice == "Cancel":
        return redirect(url_for('admin.admin_tokens', session_token=session_token))
    elif confirmation_type == "create_erc20_failed" and choice == "OK":
        return redirect(url_for('admin.admin_tokens', session_token=session_token))
    elif confirmation_type == "onboarded_new_user":
        if choice == "Administration":
            return redirect(url_for('admin.admin_main', session_token=session_token))
        else:
            return redirect(url_for('admin.create_user', session_token=session_token))
    elif confirmation_type == "reset-password":
        if choice == "Cancel":
            return redirect(url_for("admin.view_users", session_token=session_token, limit=PAGE_LIMIT, offset=0))
    elif confirmation_type == "acl_updated":
        if choice == "OK":
            return redirect(url_for("admin.view_users", session_token=session_token, limit=PAGE_LIMIT, offset=0))
    db = database.Database(logger=current_app.logger)
    user_id = db.validate_session(session_token)
    if user_id:
        user_ctx = UserContext(user_id, db, current_app.logger)
        if confirmation_type == "erc20_publish":
            token_id = int(confirmation_val)
            sc = SmartContract(smart_token_id=token_id)
            credits = Credits(user_id, db, logger=current_app.logger)
            if sc.smart_contract_id > 0:
                event_data = {"token_name": sc.token_name,
                              "token_symbol": sc.token_symbol,
                              "token_count": sc.tokens,
                              "token_id": sc.smart_contract_id}
                if user_ctx.check_acl("launch-ico"):
                    credits_balance = credits.get_credit_balance()
                    if credits_balance >= credits.erc20_publish_price:
                        new_event = Event("ERC20 Token Created", db, logger=current_app.logger)
                        event_id = new_event.log_event(user_id, event_data)
                        event_data["event_id"] = event_id
                        credits.debit(credits.erc20_publish_price, event_data)
                        command_id = db.post_command(json.dumps(event_data))
                        if command_id:
                            return redirect(url_for("admin.admin_tokens", session_token=session_token))
                        else:
                            abort(500)
                    else:
                        credits.logger.error("Insufficient credits for ERC20 Publish: "
                                             + user_ctx.user_info["email_address"])
                abort(403)
        elif confirmation_type == "reset-password":
            user_id = int(confirmation_val)
            if request.form["password"] != request.form["repeat_password"]:
                return render_template("admin/admin_confirmation.jinja2",
                                       confirmation_type="reset-password",
                                       confirmation_value=user_id,
                                       title="Reset Password",
                                       confirmation_title="Reset Password",
                                       confirmation_message="Passwords must match both times.",
                                       new_password=True,
                                       choices=["Cancel"],
                                       default_choice="Reset Password",
                                       session_token=session_token)
            if db.reset_password(int(confirmation_val), request.form["password"]):
                return redirect(url_for("admin.view_users", session_token=session_token, limit=PAGE_LIMIT, offset=0))

    abort(403)


@admin_blueprint.route('/users/new-user-acl', methods=["POST"])
def onboard_user():
    session_token = request.form["session_token"]
    db = database.Database()
    user_id = db.validate_session(session_token)
    if user_id:
        user_ctx = UserContext(user_id, db, current_app.logger)
        auth = user_ctx.check_acl("onboard-users")
        if auth:
            acl = json.loads(request.form["acl"])
            # TODO make sure the user is not issuing permissions they don't have themselves
            full_name = request.form["full_name"]
            email = request.form["email_address"]
            pw_hash = request.form["pw_hash"]
            ip_addr = request.access_route[-1]
            new_user_id = db.onboard_user(full_name, email, pw_hash, json.dumps(acl), ip_addr)
            if new_user_id:
                new_user_context = UserContext(new_user_id, db, current_app.logger)
                new_user_event = Event("Users Create User", db, current_app.logger)
                new_user_event.log_event(user_id, {"user_id": new_user_id,
                                                   "email": email,
                                                   "full_name": full_name,
                                                   "acl": json.dumps(acl)})

                db.update_user_permissions(new_user_id, json.dumps(acl))
                message = "Successfully created new user " + new_user_context.user_info["email_address"]
                return render_template("admin/admin_confirmation.jinja2",
                                       session_token=session_token,
                                       confirmation_type="onboarded_new_user",
                                       confirmation_title="Created New User",
                                       confirmation_message=message,
                                       default_choice="Create Another User",
                                       choices=["Administration"])
            else:
                abort(500)
    abort(403)


@admin_blueprint.route('/tokens/erc20_publish', methods=["POST"])
def erc20_publish():
    session_token = request.form["session_token"]
    token_id_form_field = request.form["token_id"]
    confirmation = request.form["confirmation"]
    db = database.Database()
    user_id = db.validate_session(session_token)
    if user_id:
        token_id = int(token_id_form_field)
        if token_id < 1:
            raise ValueError
        sc = SmartContract(smart_token_id=token_id)
        if sc.smart_contract_id < 1:
            abort(404)
        if confirmation == "true":
            credits = Credits(user_id, db, current_app.logger)
            current_balance = credits.get_credit_balance()
            if current_balance < credits.erc20_publish_price:
                message = "Your credit balance of <span class=\"credit_balance\">"
                message += str(current_balance) + "</span> is less than the <span class=\"credit_price\">"
                message += str(credits.erc20_publish_price) + "</span> required to publish an ERC20 token."
                message += "<p>[ <a class=\"login_anchor\" href=\"/admin/credits/purchase/"
                message += session_token + "\">purchase credits</a> ]</p>"
                return render_template("admin/admin_confirmation.jinja2",
                                       session_token=session_token,
                                       confirmation_value=token_id,
                                       confirmation_title="Insufficient Credits",
                                       confirmation_type="insufficient_credits",
                                       confirmation_message=message,
                                       default_choice="Cancel")
            message = "Are you sure you want to publish <em>" + sc.token_name + "</em> permanently to the Ethereum "
            message += "blockchain, costing <span class=\"credit_price\">"
            message += str(credits.erc20_publish_price) + "</span> credits?"
            return render_template("admin/admin_confirmation.jinja2",
                                   session_token=session_token,
                                   confirmation_value=token_id,
                                   confirmation_title="Publish ERC20 contract?",
                                   confirmation_message=message,
                                   confirmation_type="erc20_publish",
                                   choices=["Cancel"],
                                   default_choice="Publish")


@admin_blueprint.route('/tokens/create', methods=["POST"])
def create_tokens_form():
    session_token = request.form["session_token"]
    if session_token:
        db = database.Database(logger=current_app.logger)
        logger = current_app.logger
        user_id = db.validate_session(session_token)
        ctx = UserContext(user_id, db, logger)
        auth = ctx.check_acl("launch-ico")
        token_name = request.form['token_name']
        if not TOKEN_NAME_REGEX.match(token_name):
            create_token_error = "Invalid token name, must consist of 4-36 alphanumeric characters only."
            return render_template("admin/admin_confirmation.jinja2",
                                   session_token=session_token,
                                   confirmation_title="Invalid ERC20 parameter(s)",
                                   confirmation_message=create_token_error,
                                   confirmation_type="create_erc20_failed",
                                   default_choice="OK")
        token_symbol = request.form['token_symbol']
        if len(token_symbol) > 0 and not TOKEN_SYMBOL_REGEX.match(token_symbol):
            create_token_error = "Invalid token symbol, must consist of between 1-5 uppercase letters or numbers. This field is optional."
            return render_template("admin/admin_confirmation.jinja2",
                                   session_token=session_token,
                                   confirmation_title="Invalid ERC20 parameter(s)",
                                   confirmation_message=create_token_error,
                                   confirmation_type="create_erc20_failed",
                                   default_choice="OK")
        elif len(token_symbol) == 0:
            token_symbol = None
        token_count = request.form['token_count']
        if not TOKEN_COUNT_REGEX.match(token_count):
            return render_template("admin/admin_confirmation.jinja2",
                                   session_token=session_token,
                                   confirmation_title="Invalid ERC20 parameter(s)",
                                   confirmation_message="Invalid initial token count value, must be a positive integer.",
                                   confirmation_type="create_erc20_failed",
                                   default_choice="OK")
        token_count = int(token_count)
        if auth:
            sc = SmartContract(token_name=token_name,
                               token_symbol=token_symbol,
                               token_count=token_count,
                               logger=current_app.logger,
                               owner_id=user_id)
            if sc.smart_contract_id > 0:
                return redirect(url_for("admin.admin_tokens", session_token=session_token))
            abort(500)
    abort(403)


@admin_blueprint.route('/tokens/view_source/<token_id>/<session_token>')
def admin_view_source(token_id, session_token):
    token_id = int(token_id)
    db = database.Database(logger=current_app.logger)
    user_id = db.validate_session(session_token)
    if user_id and token_id > 0:
        token_info = db.get_smart_contract_info(token_id)
        if token_info["owner_id"] == user_id:
            sc = SmartContract(smart_token_id=token_id)
            return render_template("view_smart_contract.html",
                                   new_solidity_contract=sc.solidity_code)
    abort(403)


@admin_blueprint.route('/users/create', methods=["POST"])
def admin_create_user_acl():
    session_token = request.form["session_token"]
    full_name = request.form["full_name"]
    email_address = request.form["email_address"]
    password = request.form["password"]
    password_repeat = request.form["password_repeat"]
    if password == password_repeat:
        data = email_address + password
        pw_hash = sha256(data.encode("utf-8")).hexdigest()
    else:
        return render_template("admin/admin_create_user.jinja2",
                               session_token=session_token,
                               create_user_error="Password must match both times.")
    db = database.Database()
    user_id = db.validate_session(session_token)
    if user_id:
        authorized = db.validate_permission(user_id, "onboard-users")
        if authorized:
            return render_template("admin/admin_create_user_acl.jinja2",
                                   session_token=session_token,
                                   full_name=full_name,
                                   email_address=email_address,
                                   password_hash=pw_hash,
                                   new_user=True)
    abort(403)


@admin_blueprint.route('/users/change-permissions/<user_id>/<session_token>')
def change_user_permissions(user_id, session_token):
    user_id = int(user_id)
    db = database.Database()
    auth_user_id = db.validate_session(session_token)
    if auth_user_id:
        auth_ctx = UserContext(auth_user_id, db, current_app.logger)
        # TODO: there is a lot more granularity we could get here
        if auth_ctx.check_acl("change-permissions"):
            user_ctx = UserContext(user_id, db, current_app.logger)
            existing_acl = user_ctx.acl()
            return render_template("admin/admin_create_user_acl.jinja2",
                                   session_token=session_token,
                                   user_id=user_id,
                                   email_address=user_ctx.user_info['email_address'],
                                   existing_acl=json.dumps(existing_acl),
                                   new_user=False)
    abort(403)


@admin_blueprint.route('/users/change-user-acl', methods=["POST"])
def change_user_acl():
    session_token = request.form['session_token']
    acl_data = request.form['acl']
    db = database.Database()
    auth_user_id = db.validate_session(session_token)
    if auth_user_id:
        auth_user_ctx = UserContext(auth_user_id, db, current_app.logger)
        if auth_user_ctx.check_acl("change-permissions"):
            user_id = int(request.form['user_id'])
            user_ctx = UserContext(user_id, db, current_app.logger)
            user_event = Event("Users Changed Permissions", db, current_app.logger)
            event_data = {"email_address": user_ctx.user_info["email_address"],
                          "user_id": user_id,
                          "new_acl_data": acl_data}
            user_event.log_event(auth_user_id, event_data)
            result = db.update_user_permissions(user_id, json.loads(acl_data))
            if result:
                message = "Access Control List updated for " + user_ctx.user_info['email_address']
                return render_template("admin/admin_confirmation.jinja2",
                                       session_token=session_token,
                                       title="ACL Updated",
                                       confirmation_title="ACL Updated",
                                       confirmation_message=message,
                                       confirmation_type="acl_updated",
                                       default_choice="OK")
            else:
                abort(500)
    abort(403)


@admin_blueprint.route('/users/<session_token>/<offset>/<limit>')
def view_users(session_token, offset=0, limit=20):
    if session_token:
        offset = int(offset)
        limit = int(limit)
        if offset < 0 or limit < 0:
            raise ValueError
        db = database.Database()
        db.logger = current_app.logger
        user_id = db.validate_session(session_token)
        if user_id:
            user_ctx = UserContext(user_id, db, current_app.logger)
            user_data = db.list_users(offset, limit)
            user_count = db.get_user_count()
            can_reset_password = user_ctx.check_acl("reset-passwords")
            can_change_permissions = user_ctx.check_acl("change-permissions")
            can_issue_credits = user_ctx.check_acl("issue-credits")
            can_view_wallet = user_ctx.check_acl("assign-tokens") or user_ctx.check_acl("remove-tokens")

            augmented_user_data = []
            for each_user in user_data:
                new_obj = dict(each_user)
                ledger = TransactionLedger(each_user['user_id'], db, current_app.logger)
                user_credits = Credits(each_user['user_id'], db, current_app.logger)
                new_obj['transactions'] = ledger.get_transaction_count()
                new_obj['credits_balance'] = user_credits.get_credit_balance()
                new_obj['member_tokens'] = user_ctx.get_member_tokens()
                new_obj['manager_tokens'] = user_ctx.get_manager_tokens()
                issue_token_event = Event("ERC20 Token Issue", db, current_app.logger)
                new_obj['issued_tokens'] = issue_token_event.get_event_count(each_user['user_id'])
                new_obj['owned_tokens'] = ledger.get_owned_token_count()
                augmented_user_data.append(new_obj)
            return render_template("admin/admin_users.jinja2",
                                   session_token=session_token,
                                   users=augmented_user_data,
                                   reset_password=can_reset_password,
                                   change_permissions=can_change_permissions,
                                   issue_credits=can_issue_credits,
                                   view_wallet=can_view_wallet,
                                   user_count=user_count,
                                   limit=limit,
                                   offset=offset)
