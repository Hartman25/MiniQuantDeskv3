"""
Discord bot for remote control and commands.

COMMANDS:
- !status - System status
- !positions - Current positions
- !start - Start trading
- !stop - Stop trading
- !risk - Risk limits
- !account - Account info
- !help - Command list

SECURITY:
- Authorized user IDs only
- Command logging
- Confirmation for dangerous commands
"""

from typing import Optional, Dict, Callable, List
from decimal import Decimal
from datetime import datetime
import discord
from discord.ext import commands
import asyncio

from core.logging import get_logger, LogStream


# ============================================================================
# DISCORD COMMAND HANDLER
# ============================================================================

class DiscordCommandHandler:
    """
    Discord bot for remote control.
    
    USAGE:
        handler = DiscordCommandHandler(
            bot_token="...",
            authorized_users=[123456789],  # Your Discord user ID
            command_callbacks={
                "get_status": lambda: {"status": "running"},
                "get_positions": lambda: [...],
                "start_trading": lambda: True,
                "stop_trading": lambda: True
            }
        )
        
        # Run in background thread
        await handler.start()
    """
    
    def __init__(
        self,
        bot_token: str,
        authorized_users: List[int],
        command_callbacks: Dict[str, Callable]
    ):
        """
        Initialize command handler.
        
        Args:
            bot_token: Discord bot token
            authorized_users: List of authorized Discord user IDs
            command_callbacks: Dict of command_name -> callback function
        """
        self.bot_token = bot_token
        self.authorized_users = set(authorized_users)
        self.callbacks = command_callbacks
        self.logger = get_logger(LogStream.SYSTEM)
        
        # Create bot
        intents = discord.Intents.default()
        intents.message_content = True
        self.bot = commands.Bot(command_prefix="!", intents=intents)
        
        # Register commands
        self._register_commands()
        
        self.logger.info("DiscordCommandHandler initialized", extra={
            "authorized_users": len(authorized_users)
        })
    
    def _register_commands(self):
        """Register Discord commands."""
        
        @self.bot.command(name="status")
        async def status(ctx):
            """Get system status."""
            if not await self._check_authorized(ctx):
                return
            
            try:
                status_data = self.callbacks.get("get_status", lambda: {})()
                
                embed = discord.Embed(
                    title="📊 System Status",
                    color=0x3498db,
                    timestamp=datetime.utcnow()
                )
                
                for key, value in status_data.items():
                    embed.add_field(name=key.replace("_", " ").title(), value=str(value), inline=True)
                
                await ctx.send(embed=embed)
                
            except Exception as e:
                await ctx.send(f"❌ Error: {str(e)}")
        
        @self.bot.command(name="positions")
        async def positions(ctx):
            """Get current positions."""
            if not await self._check_authorized(ctx):
                return
            
            try:
                positions_data = self.callbacks.get("get_positions", lambda: [])()
                
                if not positions_data:
                    await ctx.send("No open positions")
                    return
                
                embed = discord.Embed(
                    title="📍 Current Positions",
                    color=0x9b59b6,
                    timestamp=datetime.utcnow()
                )
                
                for pos in positions_data[:25]:  # Discord limit
                    pnl_str = f"${pos.get('unrealized_pnl', 0):+,.2f}" if pos.get('unrealized_pnl') else "N/A"
                    embed.add_field(
                        name=pos.get('symbol', 'UNKNOWN'),
                        value=f"Qty: {pos.get('quantity', 0)}\nEntry: ${pos.get('entry_price', 0)}\nP&L: {pnl_str}",
                        inline=True
                    )
                
                await ctx.send(embed=embed)
                
            except Exception as e:
                await ctx.send(f"❌ Error: {str(e)}")
        
        @self.bot.command(name="start")
        async def start_trading(ctx):
            """Start trading."""
            if not await self._check_authorized(ctx):
                return
            
            # Confirmation
            await ctx.send("⚠️ Start trading? Reply `yes` to confirm (30s timeout)")
            
            def check(m):
                return m.author == ctx.author and m.channel == ctx.channel and m.content.lower() == "yes"
            
            try:
                await self.bot.wait_for("message", check=check, timeout=30.0)
                
                # Execute
                result = self.callbacks.get("start_trading", lambda: False)()
                
                if result:
                    await ctx.send("✅ Trading started")
                else:
                    await ctx.send("❌ Failed to start trading")
                    
            except asyncio.TimeoutError:
                await ctx.send("⏰ Confirmation timeout - command cancelled")
        
        @self.bot.command(name="stop")
        async def stop_trading(ctx):
            """Stop trading."""
            if not await self._check_authorized(ctx):
                return
            
            # Confirmation
            await ctx.send("⚠️ Stop trading? Reply `yes` to confirm (30s timeout)")
            
            def check(m):
                return m.author == ctx.author and m.channel == ctx.channel and m.content.lower() == "yes"
            
            try:
                await self.bot.wait_for("message", check=check, timeout=30.0)
                
                # Execute
                result = self.callbacks.get("stop_trading", lambda: False)()
                
                if result:
                    await ctx.send("🛑 Trading stopped")
                else:
                    await ctx.send("❌ Failed to stop trading")
                    
            except asyncio.TimeoutError:
                await ctx.send("⏰ Confirmation timeout - command cancelled")
        
        @self.bot.command(name="risk")
        async def risk_limits(ctx):
            """Get risk limits."""
            if not await self._check_authorized(ctx):
                return
            
            try:
                risk_data = self.callbacks.get("get_risk_limits", lambda: {})()
                
                embed = discord.Embed(
                    title="🛡️ Risk Limits",
                    color=0xf39c12,
                    timestamp=datetime.utcnow()
                )
                
                for key, value in risk_data.items():
                    embed.add_field(name=key.replace("_", " ").title(), value=str(value), inline=True)
                
                await ctx.send(embed=embed)
                
            except Exception as e:
                await ctx.send(f"❌ Error: {str(e)}")
        
        @self.bot.command(name="account")
        async def account_info(ctx):
            """Get account info."""
            if not await self._check_authorized(ctx):
                return
            
            try:
                account_data = self.callbacks.get("get_account_info", lambda: {})()
                
                embed = discord.Embed(
                    title="💰 Account Info",
                    color=0x2ecc71,
                    timestamp=datetime.utcnow()
                )
                
                for key, value in account_data.items():
                    embed.add_field(name=key.replace("_", " ").title(), value=str(value), inline=True)
                
                await ctx.send(embed=embed)
                
            except Exception as e:
                await ctx.send(f"❌ Error: {str(e)}")
        
        @self.bot.command(name="help")
        async def help_command(ctx):
            """Show commands."""
            embed = discord.Embed(
                title="📚 Available Commands",
                description="MiniQuantDesk v2 Remote Control",
                color=0x3498db
            )
            
            commands_list = [
                ("!status", "System status"),
                ("!positions", "Current positions"),
                ("!start", "Start trading (requires confirmation)"),
                ("!stop", "Stop trading (requires confirmation)"),
                ("!risk", "Risk limits"),
                ("!account", "Account information"),
                ("!help", "This help message")
            ]
            
            for cmd, desc in commands_list:
                embed.add_field(name=cmd, value=desc, inline=False)
            
            await ctx.send(embed=embed)
    
    async def _check_authorized(self, ctx) -> bool:
        """Check if user is authorized."""
        if ctx.author.id not in self.authorized_users:
            await ctx.send("❌ Unauthorized")
            self.logger.warning("Unauthorized command attempt", extra={
                "user_id": ctx.author.id,
                "username": str(ctx.author)
            })
            return False
        
        self.logger.info("Command executed", extra={
            "user_id": ctx.author.id,
            "command": ctx.command.name if ctx.command else "unknown"
        })
        
        return True
    
    async def start(self):
        """Start bot (blocking)."""
        self.logger.info("Starting Discord bot")
        await self.bot.start(self.bot_token)
    
    def stop(self):
        """Stop bot."""
        self.logger.info("Stopping Discord bot")
        asyncio.create_task(self.bot.close())
