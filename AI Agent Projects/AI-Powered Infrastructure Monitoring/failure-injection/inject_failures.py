#!/usr/bin/env python3
"""
Failure Injection Script
Simulates various infrastructure and application failures for testing
"""
import requests
import argparse
import time
import logging
from typing import Optional

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class FailureInjector:
    """Inject controlled failures into product machines"""
    
    def __init__(self, target_machine: str):
        self.target_machine = target_machine
        self.machine_ports = {
            "product-01": "8001",
            "product-02": "8002",
            "product-03": "8003"
        }
        self.base_url = f"http://localhost:{self.machine_ports.get(target_machine, '8001')}"
    
    def inject_high_cpu(self, duration: int = 60):
        """Simulate high CPU usage"""
        logger.info(f"Injecting high CPU on {self.target_machine} for {duration}s")
        # In production, would call special endpoint that spins CPU
        logger.warning("CPU injection requires application endpoint implementation")
    
    def inject_memory_pressure(self, duration: int = 60):
        """Simulate memory pressure"""
        logger.info(f"Injecting memory pressure on {self.target_machine} for {duration}s")
        logger.warning("Memory injection requires application endpoint implementation")
    
    def inject_api_latency(self, latency_ms: int = 3000, duration: int = 300):
        """Simulate API latency by generating slow requests"""
        logger.info(f"Injecting API latency on {self.target_machine}: {latency_ms}ms for {duration}s")
        
        end_time = time.time() + duration
        request_count = 0
        
        try:
            while time.time() < end_time:
                try:
                    # Hit the slow endpoint repeatedly
                    response = requests.get(f"{self.base_url}/api/v1/reports", timeout=10)
                    request_count += 1
                    if request_count % 10 == 0:
                        logger.info(f"Sent {request_count} slow requests")
                    time.sleep(0.5)
                except requests.exceptions.Timeout:
                    logger.warning("Request timed out")
                except Exception as e:
                    logger.error(f"Request error: {e}")
        except KeyboardInterrupt:
            logger.info("Injection stopped by user")
        
        logger.info(f"API latency injection completed. Total requests: {request_count}")
    
    def inject_api_errors(self, error_rate: float = 0.2, duration: int = 300):
        """Generate API errors by hitting endpoints that may fail"""
        logger.info(f"Injecting API errors on {self.target_machine} for {duration}s")
        
        end_time = time.time() + duration
        request_count = 0
        error_count = 0
        
        try:
            while time.time() < end_time:
                try:
                    # Hit endpoint that occasionally errors
                    response = requests.get(f"{self.base_url}/api/v1/data", timeout=5)
                    request_count += 1
                    
                    if response.status_code >= 500:
                        error_count += 1
                    
                    if request_count % 20 == 0:
                        logger.info(f"Requests: {request_count}, Errors: {error_count}")
                    
                    time.sleep(0.1)
                except Exception as e:
                    logger.error(f"Request error: {e}")
        except KeyboardInterrupt:
            logger.info("Injection stopped by user")
        
        logger.info(f"Error injection completed. Requests: {request_count}, Errors: {error_count}")
    
    def inject_database_load(self, duration: int = 300):
        """Generate database load by making many concurrent requests"""
        logger.info(f"Injecting database load on {self.target_machine} for {duration}s")
        
        import concurrent.futures
        
        def make_request():
            try:
                requests.get(f"{self.base_url}/api/v1/reports", timeout=10)
            except:
                pass
        
        end_time = time.time() + duration
        
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
                while time.time() < end_time:
                    # Submit many concurrent requests
                    futures = [executor.submit(make_request) for _ in range(20)]
                    time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Injection stopped by user")
        
        logger.info("Database load injection completed")
    
    def inject_traffic_spike(self, duration: int = 180):
        """Generate sudden traffic spike"""
        logger.info(f"Injecting traffic spike on {self.target_machine} for {duration}s")
        
        import concurrent.futures
        
        def make_request():
            try:
                requests.get(f"{self.base_url}/api/v1/users", timeout=5)
            except:
                pass
        
        end_time = time.time() + duration
        request_count = 0
        
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=100) as executor:
                while time.time() < end_time:
                    futures = [executor.submit(make_request) for _ in range(50)]
                    request_count += 50
                    
                    if request_count % 500 == 0:
                        logger.info(f"Generated {request_count} requests")
                    
                    time.sleep(0.5)
        except KeyboardInterrupt:
            logger.info("Injection stopped by user")
        
        logger.info(f"Traffic spike injection completed. Total requests: {request_count}")


def main():
    parser = argparse.ArgumentParser(description="Inject failures into product machines")
    parser.add_argument(
        "--machine",
        choices=["product-01", "product-02", "product-03"],
        required=True,
        help="Target machine"
    )
    parser.add_argument(
        "--failure-type",
        choices=[
            "high-cpu",
            "memory-pressure",
            "api-latency",
            "api-errors",
            "database-load",
            "traffic-spike"
        ],
        required=True,
        help="Type of failure to inject"
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=300,
        help="Duration in seconds (default: 300)"
    )
    
    args = parser.parse_args()
    
    injector = FailureInjector(args.machine)
    
    logger.info(f"Starting failure injection: {args.failure_type} on {args.machine}")
    
    if args.failure_type == "high-cpu":
        injector.inject_high_cpu(args.duration)
    elif args.failure_type == "memory-pressure":
        injector.inject_memory_pressure(args.duration)
    elif args.failure_type == "api-latency":
        injector.inject_api_latency(duration=args.duration)
    elif args.failure_type == "api-errors":
        injector.inject_api_errors(duration=args.duration)
    elif args.failure_type == "database-load":
        injector.inject_database_load(args.duration)
    elif args.failure_type == "traffic-spike":
        injector.inject_traffic_spike(args.duration)


if __name__ == "__main__":
    main()
