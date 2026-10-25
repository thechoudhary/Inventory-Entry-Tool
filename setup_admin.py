import getpass
from db import init_db, create_initial_admin, admin_count

def main():
    init_db()
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
