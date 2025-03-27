# pools_cr: Cross-Chain Bridge Event Listener Simulation

This repository contains a Python script that simulates the core logic of a relayer node for a cross-chain bridge. It is designed to be a robust, architecturally sound component for a decentralized system, demonstrating best practices in modular design, state management, and error handling.

## Concept

A cross-chain bridge allows users to transfer assets or data from one blockchain (the "source chain") to another (the "destination chain"). A common pattern is the "lock-and-mint" mechanism:

1.  A user deposits assets into a smart contract on the source chain (e.g., Ethereum). This action emits a `TokensDeposited` event.
2.  A network of off-chain nodes, called "relayers" or "oracles," listens for this event.
3.  After waiting for a certain number of block confirmations to ensure the transaction is final and not part of a chain reorganization (re-org), a relayer will submit a transaction to a corresponding smart contract on the destination chain (e.g., Polygon).
4.  This transaction calls a `mint` function, creating a wrapped or synthetic version of the asset on the destination chain and sending it to the user's address.

This script simulates the logic of **Step 2, 3 and 4**: the relayer node that securely and reliably transfers the event information from the source chain to the destination chain.

## Code Architecture

The script is designed with a clear separation of concerns, with each class handling a specific responsibility. This makes the system easier to understand, maintain, and extend.

```
[ Main (script.py) ]
       |
       +--> instantiates --> [ ConfigManager ]  (Handles .env loading and validation)
       |
       +--> instantiates --> [ CrossChainProcessor ] (The main orchestrator)
                                |
                                +---> [ EventScanner (for Source Chain) ]
                                |         |--> uses --> [ BlockchainConnector ]
                                |
                                +---> [ TransactionRelayer (for Dest Chain) ]
                                |         |--> uses --> [ BlockchainConnector ]
                                |
                                +--> calls --> run() [Main execution loop]
```

*   **`ConfigManager`**: A centralized class for loading, accessing, and validating all configuration parameters from a `.env` file. It ensures the application doesn't start with missing critical information.

*   **`BlockchainConnector`**: Manages the connection to a single blockchain via its RPC URL. It initializes the `web3` instance and loads the necessary smart contract ABI, providing a ready-to-use contract object.

*   **`EventScanner`**: The heart of the listening mechanism. It scans the source chain for new blocks, filters for the specific `TokensDeposited` event, and manages a list of "pending" events. It is responsible for handling block confirmations.

*   **`TransactionRelayer`**: Responsible for all write operations on the destination chain. It takes confirmed event data, constructs a new transaction (`mintBridgedTokens`), signs it with the relayer's private key, and broadcasts it to the network. It also handles nonce management.

*   **`CrossChainProcessor`**: The main orchestrator. It initializes all other components and contains the main application loop. It gets confirmed events from the `EventScanner` and passes them to the `TransactionRelayer` for processing, ensuring that each event is processed only once.

## How it Works

The script follows a continuous execution cycle:

1.  **Initialization**: The script starts by creating mock ABI files and loading all required configuration from the `.env` file using the `ConfigManager`.

2.  **State Restoration**: The `EventScanner` loads the last block number it scanned from a local file (`scanner_state.json`). This ensures that if the script is restarted, it doesn't re-process old events and can resume where it left off.

3.  **Health Check**: Before each scanning cycle, the `CrossChainProcessor` performs a basic health check on both the source and destination chain RPC endpoints using the `requests` library. If an endpoint is down, it will pause and retry later.

4.  **Scanning Loop**: The `EventScanner` queries the source chain for new blocks since the last scan. It fetches event logs for the configured bridge contract.

5.  **Event Detection & Confirmation**: Any new `TokensDeposited` events are added to a `pending_events` dictionary. The script then checks this dictionary against the latest block number. If an event's block number is older than the current block by at least `CONFIRMATION_BLOCKS`, it is considered final and moved to a "confirmed" list.

6.  **Relaying**: The `CrossChainProcessor` iterates through the confirmed events. For each event, it ensures it hasn't been processed before (using an in-memory cache) and then instructs the `TransactionRelayer` to build, sign, and send the corresponding `mintBridgedTokens` transaction to the destination chain.

7.  **State Update**: After scanning a batch of blocks, the `EventScanner` saves the latest block number to `scanner_state.json`.

8.  **Wait**: The script pauses for a configurable interval (`SCAN_INTERVAL_SECONDS`) before starting the next cycle.

## Usage Example

### Prerequisites
*   Python 3.8+ and `pip`
*   Access to RPC endpoints for two EVM-compatible chains (e.g., from Infura, Alchemy, or a local node).
*   A wallet private key with funds on the destination chain to pay for gas fees.

### 1. Setup

First, clone the repository and install the required dependencies:

```bash
# Clone the repository (example)
git clone https://github.com/your-username/pools_cr.git
cd pools_cr

# Install Python dependencies
pip install -r requirements.txt
```

### 2. Configuration

Create a file named `.env` in the root of the project directory and populate it with your specific details. **Never commit this file to version control.**

```dotenv
# .env file

# RPC endpoint for the source chain (e.g., Ethereum, Goerli)
SOURCE_CHAIN_RPC_URL="https://goerli.infura.io/v3/YOUR_INFURA_PROJECT_ID"

# RPC endpoint for the destination chain (e.g., Polygon, Mumbai)
DEST_CHAIN_RPC_URL="https://polygon-mumbai.infura.io/v3/YOUR_INFURA_PROJECT_ID"

# The private key of the account that will pay gas to submit minting transactions
# IMPORTANT: Use a dedicated account with limited funds. Do NOT use your main wallet.
RELAYER_PRIVATE_KEY="0x...your_private_key..."

# Address of the bridge contract on the source chain
SOURCE_BRIDGE_CONTRACT_ADDRESS="0x...source_contract_address..."

# Address of the bridge contract on the destination chain
DEST_BRIDGE_CONTRACT_ADDRESS="0x...destination_contract_address..."

# Number of blocks to wait for confirmation on the source chain (protection against re-orgs)
CONFIRMATION_BLOCKS=12

# How many seconds to wait between scanning cycles
SCAN_INTERVAL_SECONDS=30
```

### 3. Run the Script

Execute the script from your terminal:

```bash
python script.py
```

The script will start running. You will see log messages indicating its progress:

```
2023-10-27 10:00:00 - INFO - ConfigManager - Configuration loaded and validated successfully.
2023-10-27 10:00:01 - INFO - BlockchainConnector - Connected to https://goerli.infura.io/v3/... and initialized contract at 0x...
2023-10-27 10:00:02 - INFO - BlockchainConnector - Connected to https://polygon-mumbai.infura.io/v3/... and initialized contract at 0x...
2023-10-27 10:00:02 - INFO - TransactionRelayer - Transaction Relayer initialized for address: 0x...
2023-10-27 10:00:02 - INFO - CrossChainProcessor - Starting Cross-Chain Bridge Processor...
2023-10-27 10:00:05 - INFO - EventScanner - Scanning for 'TokensDeposited' events from block 9000001 to 9000101...
2023-10-27 10:00:06 - INFO - EventScanner - New pending event detected: 0x... in block 9000050
2023-10-27 10:00:36 - INFO - EventScanner - Event 0x... is now confirmed (13 confirmations).
2023-10-27 10:00:36 - INFO - CrossChainProcessor - Processing confirmed event from transaction: 0x...
2023-10-27 10:00:37 - INFO - TransactionRelayer - Preparing to mint 100000000 tokens for 0x... on destination chain.
2023-10-27 10:00:38 - INFO - TransactionRelayer - Relay transaction sent. Hash: 0x...
...
```

To stop the script, press `Ctrl+C`.
