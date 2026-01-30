"""
Discord bot - receives commands and controls system.

ARCHITECTURE:
- Discord.py bot with slash commands
- Authorization (user ID whitelist)
- System control (start/stop/status)
- Emergency kill switch
- Position queries
- Configuration updates

COMMANDS:
- /status - System health
- /positions - Active positions
- /pnl - Today's P&L
- /start - Start trading
- /stop - Stop trading
- /kill - Emergency shutdown
- /config - View/update config

Based on production command & control patterns.
"""

from typing import Optional, Dict, List, Callable
from decimal import Decimal
from datetime import datetime
import asyncio
import threading

import discord
from discord import app_commands
from discord.ext import commands

from core.logging import get_logger, LogStream


# ============================================================================
# DISCORD BOT
# ============================================================================

class TradingBot(commands.Bot):
    """
    Discord bot for system control.
    
    FEATURES:
    - Slash commands
    - User authorization
    - System control
    - Position queries
    - Emergency shutdown
    
    USAGE:
        bot = TradingBot(
            token="...",
            authorized_users=[123456789],
            system_controller=system
        )
        
        bot.start_bot()  # Runs in background thread
    """
    
    def __init__(
        self,
        token: str,
        authorized_users: List[int],
        system_controller: Optional['SystemController'] = None
    ):
        """
        Initialize bot.
        
        Args:
            token: Discord bot token
            authorized_users: List of authorized user IDs
            system_controller: System controller instance
        """
        intents = discord.Intents.default()
        intents.message_content = True
        
        super().__init__(command_prefix="!", intents=intents)
        
        self.bot_token = token
        self.authorized_users = set(authorized_users)
        self.system_controller = system_controller
        self.logger = get_logger(LogStream.SYSTEM)
        
        # Background thread
        self._bot_thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        
        # Register commands
        self._register_commands()
        
        self.logger.info("TradingBot initialized", extra={
            "authorized_users": len(authorized_users)
        })
    
    def start_bot(self):
        """Start bot in background thread."""
        self._bot_thread = threading.Thread(target=self._run_bot, daemon=True)
        self._bot_thread.start()
        self.logger.info("Discord bot started")
    
    def stop_bot(self):
        """Stop bot."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.close(), self._loop)
        
        if self._bot_thread and self._bot_thread.is_alive():
            self._bot_thread.join(timeout=5)
        
        self.logger.info("Discord bot stopped")
    
    def _run_bot(self):
        """Run bot (blocking)."""
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self.start(self.bot_token))
        except Exception as e:
            self.logger.error("Bot error", extra={"error": str(e)}, exc_info=True)
    
    def _register_commands(self):
        """Register slash commands."""
        
        @self.tree.command(name="status", description="Get system status")
        async def status_command(interaction: discord.Interaction):
            if not self._check_authorization(interaction.user.id):
                await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
                return
            
            if not self.system_controller:
                await interaction.response.send_message("❌ System controller not available", ephemeral=True)
                return
            
            status = self.system_controller.get_status()
            
            embed = discord.Embed(
                title="📊 System Status",
                color=0x00FF00 if status.get('running') else 0xFF0000,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(name="Status", value=status.get('status', 'UNKNOWN'), inline=True)
            embed.add_field(name="Mode", value=status.get('mode', 'UNKNOWN'), inline=True)
            embed.add_field(name="Uptime", value=status.get('uptime', 'N/A'), inline=True)
            embed.add_field(name="Positions", value=str(status.get('positions', 0)), inline=True)
            embed.add_field(name="Today P&L", value=f"${status.get('pnl', 0):,.2f}", inline=True)
            
            await interaction.response.send_message(embed=embed)
        
        @self.tree.command(name="positions", description="Get active positions")
        async def positions_command(interaction: discord.Interaction):
            if not self._check_authorization(interaction.user.id):
                await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
                return
            
            if not self.system_controller:
                await interaction.response.send_message("❌ System controller not available", ephemeral=True)
                return
            
            positions = self.system_controller.get_positions()
            
            if not positions:
                await interaction.response.send_message("No active positions")
                return
            
            embed = discord.Embed(
                title="📈 Active Positions",
                color=0x0099FF,
                timestamp=datetime.utcnow()
            )
            
            for pos in positions[:10]:  # Limit to 10
                pnl = pos.get('pnl', 0)
                emoji = "💰" if pnl > 0 else "💸" if pnl < 0 else "➖"
                
                embed.add_field(
                    name=f"{emoji} {pos['symbol']}",
                    value=f"Qty: {pos['quantity']}\nEntry: ${pos['entry_price']}\nP&L: ${pnl:,.2f}",
                    inline=True
                )
            
            await interaction.response.send_message(embed=embed)
        
        @self.tree.command(name="pnl", description="Get today's P&L")
        async def pnl_command(interaction: discord.Interaction):
            if not self._check_authorization(interaction.user.id):
                await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
                return
            
            if not self.system_controller:
                await interaction.response.send_message("❌ System controller not available", ephemeral=True)
                return
            
            pnl_data = self.system_controller.get_pnl()
            
            embed = discord.Embed(
                title="💰 Today's P&L",
                color=0x00FF00 if pnl_data.get('total', 0) > 0 else 0xFF0000,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(name="Total P&L", value=f"${pnl_data.get('total', 0):,.2f}", inline=True)
            embed.add_field(name="Realized", value=f"${pnl_data.get('realized', 0):,.2f}", inline=True)
            embed.add_field(name="Unrealized", value=f"${pnl_data.get('unrealized', 0):,.2f}", inline=True)
            embed.add_field(name="Trades", value=str(pnl_data.get('trades', 0)), inline=True)
            embed.add_field(name="Win Rate", value=f"{pnl_data.get('win_rate', 0):.1%}", inline=True)
            
            await interaction.response.send_message(embed=embed)
        
        @self.tree.command(name="start", description="Start trading")
        async def start_command(interaction: discord.Interaction):
            if not self._check_authorization(interaction.user.id):
                await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
                return
            
            if not self.system_controller:
                await interaction.response.send_message("❌ System controller not available", ephemeral=True)
                return
            
            success = self.system_controller.start_trading()
            
            if success:
                await interaction.response.send_message("✅ Trading started")
            else:
                await interaction.response.send_message("❌ Failed to start trading")
        
        @self.tree.command(name="stop", description="Stop trading")
        async def stop_command(interaction: discord.Interaction):
            if not self._check_authorization(interaction.user.id):
                await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
                return
            
            if not self.system_controller:
                await interaction.response.send_message("❌ System controller not available", ephemeral=True)
                return
            
            success = self.system_controller.stop_trading()
            
            if success:
                await interaction.response.send_message("✅ Trading stopped")
            else:
                await interaction.response.send_message("❌ Failed to stop trading")
        
        @self.tree.command(name="kill", description="Emergency shutdown")
        async def kill_command(interaction: discord.Interaction):
            if not self._check_authorization(interaction.user.id):
                await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
                return
            
            await interaction.response.send_message("🚨 EMERGENCY SHUTDOWN INITIATED")
            
            if self.system_controller:
                self.system_controller.emergency_shutdown()
    
    def _check_authorization(self, user_id: int) -> bool:
        """Check if user is authorized."""
        return user_id in self.authorized_users
    
    async def on_ready(self):
        """Bot ready event."""
        self.logger.info(f"Bot logged in as {self.user}")
        
        # Sync commands
        try:
            synced = await self.tree.sync()
            self.logger.info(f"Synced {len(synced)} commands")
        except Exception as e:
            self.logger.error("Failed to sync commands", extra={"error": str(e)})


# ============================================================================
# SYSTEM CONTROLLER INTERFACE
# ============================================================================

class SystemController:
    """
    Interface for bot to control system.
    
    Implement this interface in your main system class.
    """
    
    def get_status(self) -> Dict:
        """Get system status."""
        raise NotImplementedError
    
    def get_positions(self) -> List[Dict]:
        """Get active positions."""
        raise NotImplementedError
    
    def get_pnl(self) -> Dict:
        """Get P&L data."""
        raise NotImplementedError
    
    def start_trading(self) -> bool:
        """Start trading."""
        raise NotImplementedError
    
    def stop_trading(self) -> bool:
        """Stop trading."""
        raise NotImplementedError
    
    def emergency_shutdown(self):
        """Emergency shutdown."""
        raise NotImplementedError
