import logging
import logging.handlers
import sys
import os
from typing import Optional

def setup_logging(log_level: str = "INFO", log_file: Optional[str] = None) -> logging.Logger:
    """设置日志配置"""
    
    # 创建格式化器
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # 配置根日志记录器
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper()))
    
    # 清除现有的处理器
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # 文件处理器（如果指定了日志文件）
    if log_file:
        # 确保日志目录存在
        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)
            
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    
    return root_logger

def get_logger(name: str) -> logging.Logger:
    """获取指定名称的日志记录器"""
    return logging.getLogger(name)

# 预定义的日志记录器
def get_exchange_logger(exchange_name: str) -> logging.Logger:
    """获取交易所专用的日志记录器"""
    return get_logger(f"exchange.{exchange_name}")

def get_strategy_logger(strategy_name: str) -> logging.Logger:
    """获取策略专用的日志记录器"""
    return get_logger(f"strategy.{strategy_name}")

def get_arbitrage_logger() -> logging.Logger:
    """获取套利引擎专用的日志记录器"""
    return get_logger("arbitrage.engine")