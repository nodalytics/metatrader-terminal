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
docker rm -f metatrader5-terminal
docker run -d --name metatrader5-terminal \
  -p 18812:18812 \
  -p 8000:8000 \
  metatrader5-terminal
```

## Contributing

Contributions are welcome! Please fork the repository and submit a pull request.
