import getpass
import argparse
from db import init_db, create_initial_admin, admin_count, change_password, list_users

def main():
    parser = argparse.ArgumentParser(description="Create or reset an inventory administrator.")
    parser.add_argument("--reset-password", metavar="USERNAME",
                        help="reset an existing user's password")
    args = parser.parse_args()

    init_db()
    if args.reset_password:
        target = next((u for u in list_users()
                       if u["username"].casefold() == args.reset_password.casefold()), None)
        if not target:
            print(f"User {args.reset_password!r} was not found.")
            return
        password = getpass.getpass("New password (minimum 10 characters): ")
        confirm = getpass.getpass("Confirm new password: ")
        if password != confirm:
            print("Passwords do not match.")
            return
        try:
            change_password(target["id"], password)
            print(f"Password reset for {target['username']}. You can now sign in.")
        except Exception as exc:
            print(f"Could not reset password: {exc}")
        return

    if admin_count():
        print("An active administrator already exists. No setup needed.")
        return
    print("Create the initial administrator account.")
    username = input("Username (3–40 characters): ").strip()
    password = getpass.getpass("Password (minimum 10 characters): ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords do not match.")
        return
    try:
        create_initial_admin(username, password)
        print("Administrator created. Start the app with: streamlit run app.py")
    except Exception as exc:
        print(f"Could not create administrator: {exc}")

if __name__ == "__main__":
    main()
