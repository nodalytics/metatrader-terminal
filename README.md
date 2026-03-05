# Metatrader Terminals

Tools and Scripts for packaging metatrader client terminals into a docker image.

## Installation

To install the necessary dependencies, run:
```bash
docker build -t metatrader5-terminal ./MT5
```

## Usage

To start the Metatrader terminal, use:
```bash
# Run with custom credentials (optional)
docker run -d --name metatrader5-terminal \
  -e VNC_USER=mt5_user \
  -e VNC_PASSWORD=password \
  -p 18812:18812 \
  -p 6901:6901 \
  metatrader5-terminal
```

## Contributing

Contributions are welcome! Please fork the repository and submit a pull request.
