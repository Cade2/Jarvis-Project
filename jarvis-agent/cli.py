from agent.core import handle_user_message


def main():
    print("Jarvis CLI v0")
    print("Type 'exit' or 'quit' to leave.\n")

    while True:
        user_input = input("You: ")
        if user_input.strip().lower() in ("exit", "quit"):
            print("Jarvis: Goodbye!")
            break

        handle_user_message(user_input)


if __name__ == "__main__":
    main()
