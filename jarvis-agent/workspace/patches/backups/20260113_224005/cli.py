from agent.core import handle_user_message
from agent.safety import init_audit_session

def main():
    log_path = init_audit_session("logs")
    print("Jarvis CLI v0")
    print(f"Audit session: {log_path}")
    print("Type 'exit' or 'quit' to leave.\n")

    while True:
        user_input = input("You: ")
        if user_input.strip().lower() in ("exit", "quit"):
            print("Jarvis: Goodbye!")
            break

        handle_user_message(user_input)

if __name__ == "__main__":
    main()
