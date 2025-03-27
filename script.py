import os
import json
import time
import logging
import sys
from typing import Dict, Any, Optional, List

from dotenv import load_dotenv
from web3 import Web3
from web3.middleware import geth_poa_middleware
from web3.types import LogReceipt
import requests
from requests.exceptions import RequestException

# --- Basic Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(module)s - %(message)s',
    stream=sys.stdout
)

# Load environment variables from .env file
load_dotenv()

# --- Constants ---
STATE_FILE = 'scanner_state.json'

class ConfigManager:
    """A dedicated class to manage and validate configuration from environment variables."""
    def __init__(self):
        """Initializes the ConfigManager and loads all required variables."""
        self.source_rpc_url = os.getenv("SOURCE_CHAIN_RPC_URL")
        self.dest_rpc_url = os.getenv("DEST_CHAIN_RPC_URL")
        self.relayer_private_key = os.getenv("RELAYER_PRIVATE_KEY")
        self.source_bridge_address = os.getenv("SOURCE_BRIDGE_CONTRACT_ADDRESS")
        self.dest_bridge_address = os.getenv("DEST_BRIDGE_CONTRACT_ADDRESS")
        self.confirmation_blocks = int(os.getenv("CONFIRMATION_BLOCKS", 12))
        self.scan_interval_seconds = int(os.getenv("SCAN_INTERVAL_SECONDS", 15))
        self.source_abi_path = 'source_abi.json'
        self.dest_abi_path = 'dest_abi.json'
        self.validate()

    def validate(self):
        """Validates that all necessary configuration variables are present."""
        required_vars = [
            'source_rpc_url', 'dest_rpc_url', 'relayer_private_key',
            'source_bridge_address', 'dest_bridge_address'
        ]
        missing_vars = [var for var in required_vars if not getattr(self, var)]
        if missing_vars:
            message = f"Missing required environment variables: {', '.join(missing_vars)}"
            logging.error(message)
            raise ValueError(message)
        logging.info("Configuration loaded and validated successfully.")

class BlockchainConnector:
    """Handles connection to a specific blockchain and contract interactions."""
    def __init__(self, rpc_url: str, contract_address: str, abi_path: str):
        """Establishes connection to the RPC endpoint and initializes the contract object.

        Args:
            rpc_url (str): The HTTP RPC URL for the blockchain node.
            contract_address (str): The address of the target smart contract.
            abi_path (str): The file path to the contract's ABI JSON.
        """
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        # Inject middleware for PoA chains like Goerli, Sepolia, or Polygon
        self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)

        if not self.w3.is_connected():
            raise ConnectionError(f"Failed to connect to blockchain node at {rpc_url}")

        self.contract_address = self.w3.to_checksum_address(contract_address)
        self.contract = self.w3.eth.contract(
            address=self.contract_address,
            abi=self._load_abi(abi_path)
        )
        logging.info(f"Connected to {rpc_url} and initialized contract at {contract_address}")

    def _load_abi(self, abi_path: str) -> List[Dict[str, Any]]:
        """Loads a contract ABI from a JSON file."""
        try:
            with open(abi_path, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            logging.error(f"ABI file not found at {abi_path}")
            raise
        except json.JSONDecodeError:
            logging.error(f"Could not decode JSON from ABI file at {abi_path}")
            raise

class EventScanner:
    """Scans a blockchain for specific events from a smart contract."""
    def __init__(self, connector: BlockchainConnector, event_name: str, state_file: str, confirmations: int):
        """
        Args:
            connector (BlockchainConnector): The connector for the chain to scan.
            event_name (str): The name of the event to listen for.
            state_file (str): Path to the file for persisting the last scanned block.
            confirmations (int): Number of blocks to wait before considering an event confirmed.
        """
        self.connector = connector
        self.event_name = event_name
        self.state_file = state_file
        self.confirmations = confirmations
        self.event_filter = self.connector.contract.events[event_name].create_filter(fromBlock='latest')
        self.last_scanned_block = self._load_last_scanned_block()
        self.pending_events: Dict[str, Dict[str, Any]] = {}

    def _load_last_scanned_block(self) -> int:
        """Loads the last scanned block number from the state file."""
        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)
                return state.get('last_scanned_block', self.connector.w3.eth.block_number - 1)
        except (FileNotFoundError, json.JSONDecodeError):
            logging.warning(f"State file {self.state_file} not found or invalid. Starting scan from latest block.")
            return self.connector.w3.eth.block_number - 1

    def _save_last_scanned_block(self, block_number: int):
        """Saves the last scanned block number to the state file."""
        with open(self.state_file, 'w') as f:
            json.dump({'last_scanned_block': block_number}, f)

    def scan_and_process_blocks(self) -> List[LogReceipt]:
        """Scans for new blocks, finds events, and confirms them.
        
        Returns:
            List[LogReceipt]: A list of events that have met the confirmation requirement.
        """
        latest_block = self.connector.w3.eth.block_number
        if self.last_scanned_block >= latest_block:
            logging.info("No new blocks to scan.")
            return []

        from_block = self.last_scanned_block + 1
        # Scan in chunks to avoid overwhelming the RPC node
        to_block = min(latest_block, from_block + 100)

        logging.info(f"Scanning for '{self.event_name}' events from block {from_block} to {to_block}...")
        try:
            logs = self.connector.contract.events[self.event_name].get_logs(
                fromBlock=from_block,
                toBlock=to_block
            )
        except Exception as e:
            logging.error(f"Error fetching event logs: {e}")
            return []

        for event in logs:
            tx_hash = event['transactionHash'].hex()
            if tx_hash not in self.pending_events:
                self.pending_events[tx_hash] = event
                logging.info(f"New pending event detected: {tx_hash} in block {event['blockNumber']}")

        confirmed_events = self._check_confirmations(latest_block)
        
        self.last_scanned_block = to_block
        self._save_last_scanned_block(to_block)

        return confirmed_events

    def _check_confirmations(self, current_block: int) -> List[LogReceipt]:
        """Checks pending events against the current block height for confirmations."""
        confirmed_events = []
        events_to_remove = []
        for tx_hash, event in self.pending_events.items():
            block_number = event['blockNumber']
            if current_block - block_number >= self.confirmations:
                logging.info(f"Event {tx_hash} is now confirmed ({current_block - block_number} confirmations).")
                confirmed_events.append(event)
                events_to_remove.append(tx_hash)
        
        # Clean up confirmed events from the pending dictionary
        for tx_hash in events_to_remove:
            del self.pending_events[tx_hash]
            
        return confirmed_events

class TransactionRelayer:
    """Handles the creation, signing, and sending of transactions to a destination chain."""
    def __init__(self, connector: BlockchainConnector, private_key: str):
        """
        Args:
            connector (BlockchainConnector): The connector for the destination chain.
            private_key (str): The private key of the relayer account.
        """
        self.connector = connector
        self.w3 = connector.w3
        self.account = self.w3.eth.account.from_key(private_key)
        self.address = self.account.address
        logging.info(f"Transaction Relayer initialized for address: {self.address}")

    def relay_mint_transaction(self, event: LogReceipt) -> Optional[str]:
        """Constructs and sends a 'mint' transaction based on a source chain event.
        
        Args:
            event (LogReceipt): The confirmed event from the source chain.

        Returns:
            Optional[str]: The transaction hash if successful, otherwise None.
        """
        try:
            # Extract details from the source event
            recipient = event['args']['recipient']
            amount = event['args']['amount']
            source_tx_hash = event['transactionHash']

            logging.info(f"Preparing to mint {amount} tokens for {recipient} on destination chain.")

            # Build the transaction
            nonce = self.w3.eth.get_transaction_count(self.address)
            tx_data = self.connector.contract.functions.mintBridgedTokens(
                recipient, amount, source_tx_hash
            ).build_transaction({
                'from': self.address,
                'nonce': nonce,
                'gas': 200000, # A sensible default, can be estimated
                'gasPrice': self.w3.eth.gas_price
            })

            # Sign and send
            signed_tx = self.w3.eth.account.sign_transaction(tx_data, self.account.key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            
            logging.info(f"Relay transaction sent. Hash: {tx_hash.hex()}")
            
            # Optional: Wait for receipt
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            if receipt.status == 1:
                logging.info(f"Relay transaction successful! Block: {receipt.blockNumber}")
                return tx_hash.hex()
            else:
                logging.error(f"Relay transaction failed! TX Hash: {tx_hash.hex()}")
                return None

        except Exception as e:
            logging.error(f"An error occurred while relaying transaction: {e}")
            return None

class CrossChainProcessor:
    """Orchestrates the entire cross-chain listening and relaying process."""
    def __init__(self, config: ConfigManager):
        """
        Args:
            config (ConfigManager): The application configuration object.
        """
        self.config = config
        self.processed_txs_cache = set()

        # Setup source chain components
        self.source_connector = BlockchainConnector(
            rpc_url=config.source_rpc_url,
            contract_address=config.source_bridge_address,
            abi_path=config.source_abi_path
        )
        self.event_scanner = EventScanner(
            connector=self.source_connector,
            event_name='TokensDeposited',  # Assumed event name
            state_file=STATE_FILE,
            confirmations=config.confirmation_blocks
        )

        # Setup destination chain components
        self.dest_connector = BlockchainConnector(
            rpc_url=config.dest_rpc_url,
            contract_address=config.dest_bridge_address,
            abi_path=config.dest_abi_path
        )
        self.tx_relayer = TransactionRelayer(
            connector=self.dest_connector,
            private_key=config.relayer_private_key
        )

    def _is_rpc_healthy(self, url: str) -> bool:
        """Performs a basic health check on an RPC endpoint using requests."""
        try:
            payload = {"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}
            response = requests.post(url, json=payload, timeout=5)
            response.raise_for_status()
            if 'result' in response.json():
                return True
            return False
        except RequestException as e:
            logging.warning(f"RPC health check failed for {url}: {e}")
            return False

    def run(self):
        """The main execution loop for the processor."""
        logging.info("Starting Cross-Chain Bridge Processor...")
        while True:
            try:
                # Health check before processing
                if not self._is_rpc_healthy(self.config.source_rpc_url) or \
                   not self._is_rpc_healthy(self.config.dest_rpc_url):
                    logging.error("One or more RPC nodes are unhealthy. Pausing for 60 seconds.")
                    time.sleep(60)
                    continue

                confirmed_events = self.event_scanner.scan_and_process_blocks()
                
                for event in confirmed_events:
                    source_tx_hash = event['transactionHash'].hex()
                    if source_tx_hash in self.processed_txs_cache:
                        logging.warning(f"Event {source_tx_hash} has already been processed. Skipping.")
                        continue

                    logging.info(f"Processing confirmed event from transaction: {source_tx_hash}")
                    relay_tx_hash = self.tx_relayer.relay_mint_transaction(event)

                    if relay_tx_hash:
                        self.processed_txs_cache.add(source_tx_hash)
                        logging.info(f"Successfully processed source tx {source_tx_hash} with relay tx {relay_tx_hash}")
                    else:
                        logging.error(f"Failed to process source tx {source_tx_hash}. Will be retried in the next cycle.")

                logging.info(f"Cycle complete. Waiting for {self.config.scan_interval_seconds} seconds...")
                time.sleep(self.config.scan_interval_seconds)

            except KeyboardInterrupt:
                logging.info("Shutdown signal received. Exiting gracefully.")
                break
            except Exception as e:
                logging.critical(f"An unexpected critical error occurred in the main loop: {e}", exc_info=True)
                time.sleep(60) # Wait a minute before retrying on critical failure

def setup_mock_abi_files():
    """Creates mock ABI files for demonstration purposes."""
    source_abi = [
        {
            "anonymous": False, "inputs": [
                {"indexed": True, "internalType": "address", "name": "sender", "type": "address"},
                {"indexed": True, "internalType": "address", "name": "recipient", "type": "address"},
                {"indexed": False, "internalType": "uint256", "name": "amount", "type": "uint256"}
            ], "name": "TokensDeposited", "type": "event"
        }
    ]
    dest_abi = [
        {
            "inputs": [
                {"internalType": "address", "name": "recipient", "type": "address"},
                {"internalType": "uint256", "name": "amount", "type": "uint256"},
                {"internalType": "bytes32", "name": "sourceTxHash", "type": "bytes32"}
            ], "name": "mintBridgedTokens", "outputs": [], "stateMutability": "nonpayable", "type": "function"
        }
    ]
    with open('source_abi.json', 'w') as f:
        json.dump(source_abi, f)
    with open('dest_abi.json', 'w') as f:
        json.dump(dest_abi, f)
    logging.info("Mock ABI files 'source_abi.json' and 'dest_abi.json' created.")

if __name__ == "__main__":
    # This function creates placeholder ABI files required by the script.
    # In a real-world scenario, these would be actual contract ABIs.
    setup_mock_abi_files()

    try:
        config = ConfigManager()
        processor = CrossChainProcessor(config)
        processor.run()
    except ValueError as e:
        logging.critical(f"Failed to initialize application due to config error: {e}")
        sys.exit(1)
    except Exception as e:
        logging.critical(f"Application failed to start: {e}", exc_info=True)
        sys.exit(1)
