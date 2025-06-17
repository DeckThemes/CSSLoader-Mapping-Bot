import discord
import logging
import os
import aiohttp
import asyncio
import re
import io
import json

intents = discord.Intents.none()
intents.guild_messages = True
intents.message_content = True
logger = logging.getLogger('discord.bot')
base_url = os.getenv('BASE_URL', 'https://api.deckthemes.com/mappings.json')
bot_token = os.getenv('BOT_TOKEN')
if bot_token is None:
    raise Exception('BOT_TOKEN env var not set')

class MappingsManager:
    def __init__(self):
        self.module_mappings = {}
        self.index_webpack_key : dict[str, list[tuple[str, str, str]]] = {}
        self.index_css_class : dict[str, tuple[str, str, str]] = {}
        self.updated_at = ""
        self.versions = {}
        self.latest_mapped_beta_version = ""
        self.latest_mapped_stable_version = ""
    
    async def update_mappings(self):
        while True:
            if len(self.module_mappings) > 0:
                await asyncio.sleep(60 * 60 * 24) # Every day

            logger.info("Fetching new version of mapping")
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(base_url) as response:
                        response.raise_for_status()
                        data = await response.json()

                for x in ["versions", "module_mappings", "generated"]:
                    if x not in data:
                        raise Exception(f"{x} not found in returned json")
                    
            except Exception as e:
                logger.error(f"Failed to fetch mappings: {str(e)}")
                await asyncio.sleep(5)
                continue
            
            index_webpack_key = {}
            index_css_class = {}

            for _, (module_id, module_data) in enumerate(data["module_mappings"].items()):
                i = module_data["ids"][next(iter(module_data["ids"]))]
                for _, (webpack_key, css_class_list) in enumerate(module_data["classname_mappings"].items()):
                    t = (module_id, webpack_key)

                    if webpack_key not in index_webpack_key:
                        index_webpack_key[webpack_key] = []
                    
                    index_webpack_key[webpack_key].append(t)
                    index_css_class["_" + i + "_" + webpack_key] = t
                    for _, (steam_version, css_class) in enumerate(css_class_list.items()):
                        if css_class in index_css_class:
                            logger.warning(f"Duplicate css class {css_class} ({index_css_class[css_class][1]}, {webpack_key})")

                        index_css_class[css_class] = t

            self.module_mappings = data["module_mappings"]
            self.index_webpack_key = index_webpack_key
            self.index_css_class = index_css_class
            self.updated_at = data["generated"]
            self.versions = data["versions"]
            self.latest_mapped_stable_version = sorted([x for x in self.versions if self.versions[x] == "stable"], key=lambda x : int(x))[-1]
            self.latest_mapped_beta_version = sorted([x for x in self.versions if self.versions[x] == "beta"], key=lambda x : int(x))[-1]
            logger.info(f"Fetched {len(self.index_css_class)} css translations")
    
    def find_module(self, module_id : str) -> dict|None:
        if module_id not in self.module_mappings:
            return None

        return self.module_mappings[module_id]

    def find_css_class(self, css_class : str) -> tuple[dict, str, dict[str, str]]|None:
        if css_class not in self.index_css_class:
            return None
        
        match = self.index_css_class[css_class]
        module = self.find_module(match[0])
        return (module, match[1], module["classname_mappings"][match[1]])
    
    def find_webpack_key(self, webpack_key : str) -> list[tuple[dict, str, dict[str, str]]]|None:
        if webpack_key not in self.index_webpack_key:
            return None
        
        entries = []
        for match in self.index_webpack_key[webpack_key]:
            module = self.find_module(match[0])
            entries.append((module, match[1], module["classname_mappings"][match[1]]))

        return entries
    
    def get_universal_key_for_css_class(self, css_class : str) -> str|None:
        css = self.find_css_class(css_class)
        if css == None:
            return None
        
        module = css[0]
        module_id = list(module["ids"].values())[0]
        module_name = ("_" + str(module_id) if module['name'] is None else module['name'])

        return f"{module_name}_{css[1]}"


mappings_manager_instance = MappingsManager()
#asyncio.run(mappings_manager_instance.update_mappings())

class MyClient(discord.Client):
    def __init__(self, *, intents: discord.Intents):
        super().__init__(intents=intents)
        self.tree = discord.app_commands.CommandTree(self)
        self.css_group = discord.app_commands.Group(name='css', description='Look up css mappings for specific classes')
        self.tree.add_command(self.css_group)

    async def setup_hook(self):
        await self.tree.sync()

bot = MyClient(intents=intents)

def module_embed(module : dict) -> discord.Embed:
    module_name = module["name"] if module["name"] != None else ""
    other_webpack_keys = [f"`{x}`" for x in module['classname_mappings']]
    ignore_webpack_keys = [f"`{x}`" for x in module['ignore_webpack_keys']]
    embed = discord.Embed(colour=0xFF0000, title=f"Module {module_name}", description=f"- IDs: {', '.join(set(module['ids'].values()))}\n- Steam versions: {', '.join(set(module['ids'].keys()))}\n- Webpack keys: {', '.join(other_webpack_keys)}\n- Ignored keys: {', '.join(ignore_webpack_keys)}")
    return embed

def entry_embed(module : dict, webpack_key : str, webpack_mappings: dict[str, str]) -> discord.Embed:
    unkown = "unknown"
    module_id = list(module["ids"].values())[0]
    module_name = ("_" + str(module_id) if module['name'] is None else module['name'])
    embed = discord.Embed(colour=0x00FF00, title=f"Webpack key {webpack_key}", description=f"Cross-version css class: `.{module_name}_{webpack_key}`\n\n" + "\n".join([f'{steam_version} ({mappings_manager_instance.versions[steam_version] if steam_version in mappings_manager_instance.versions else unkown}) -> `{css_class}`' for _, (steam_version, css_class) in enumerate(webpack_mappings.items())]))
    return embed


async def css_autocomplete(interaction: discord.Interaction, current: str) -> list[discord.app_commands.Choice[str]]:
    result = []
    try:
        if len(current) <= 0:
            return result
        
        current_lower = current.lower()
        for x in mappings_manager_instance.index_css_class.keys():
            if current_lower in x.lower():
                result.append(discord.app_commands.Choice(name=x, value=x))
            
            if len(result) >= 25:
                break
    except Exception as e:
        logger.error(str(e))

    return result

async def webpack_key_autocomplete(interaction: discord.Interaction, current: str) -> list[discord.app_commands.Choice[str]]:
    result = []

    try:
        if len(current) <= 0:
            return result
        
        current_lower = current.lower()
        for x in mappings_manager_instance.index_webpack_key.keys():
            if current_lower in x.lower():
                result.append(discord.app_commands.Choice(name=x, value=x))
            
            if len(result) >= 25:
                break
    except Exception as e:
        result = []
        logger.error(str(e))

    return result


@bot.css_group.command(name='class', description='Find related info to a css class')
@discord.app_commands.autocomplete(css_class=css_autocomplete)
async def print_complete_command(interaction: discord.Interaction, css_class : str):
    css = mappings_manager_instance.find_css_class(css_class)
    module = module_embed(css[0])
    entry = entry_embed(css[0], css[1], css[2])
    await interaction.response.send_message(embeds=[module, entry])

@bot.css_group.command(name='webpack', description='Find related info to a webpack key')
@discord.app_commands.autocomplete(webpack_key=webpack_key_autocomplete)
async def print_complete_command(interaction: discord.Interaction, webpack_key : str):
    webpack = mappings_manager_instance.find_webpack_key(webpack_key)
    embeds = []
    truncuated = False
    for x in webpack:
        module = module_embed(x[0])
        entry = entry_embed(x[0], x[1], x[2])
        embeds.append(module)
        embeds.append(entry)

        if len(embeds) >= 6:
            truncuated = True
            break

    if truncuated:
        await interaction.response.send_message(content=f"Embeds truncated. Not all entries shown. Total {len(webpack)} found.", embeds=embeds)
    else:
        await interaction.response.send_message(embeds=embeds)

@bot.css_group.command(name="status", description="List the last mapped steam versions")
async def status(interaction: discord.Interaction):
    steam_versions = [f"{steam_version} ({version_type})" for _, (steam_version, version_type) in enumerate(mappings_manager_instance.versions.items())]
    await interaction.response.send_message(f"Currently loaded mapping: {mappings_manager_instance.updated_at}\nLast known stable version: {mappings_manager_instance.latest_mapped_stable_version}\nLast known beta version: {mappings_manager_instance.latest_mapped_beta_version}\nMapped CSS class count: {len(mappings_manager_instance.index_css_class)}\n\nSteam versions mapped:\n- {'\n- '.join(steam_versions)}")

@bot.css_group.command(name="convert", description="Convert a .css file to use cross-version css classes. File is not shared to the channel.")
async def convert(interaction : discord.Interaction, file: discord.Attachment):
    await interaction.response.defer(ephemeral=True)

    css = (await file.read()).decode()
    split_css = re.split(r"(\.[_a-zA-Z]+[_a-zA-Z0-9-]*)", css)

    for x in range(len(split_css)):
        if split_css[x].startswith(".") and split_css[x][1:] in mappings_manager_instance.index_css_class:
            split_css[x] = "." + mappings_manager_instance.get_universal_key_for_css_class(split_css[x][1:])

    css = ("".join(split_css)).replace("\\", "\\\\").replace("`", "\\`")

    split_css = re.split(r"(\[class[*^|~]=\"[_a-zA-Z0-9-]*\"\])", css)

    for x in range(len(split_css)):
        if split_css[x].startswith("[class") and split_css[x].endswith("\"]") and split_css[x][9:-2] in mappings_manager_instance.index_css_class:
            split_css[x] = split_css[x][0:9] + mappings_manager_instance.get_universal_key_for_css_class(split_css[x][9:-2]) + split_css[x][-2:]

    css = ("".join(split_css)).replace("\\", "\\\\").replace("`", "\\`")
    bio = io.BytesIO(css.encode("utf-8"))
    discord_file = discord.File(bio, filename=file.filename)

    await interaction.followup.send(file=discord_file)

# Json validator context action as emerald asked for it
@bot.tree.context_menu(name="Validate JSON")
async def your_command_func(interaction: discord.Interaction, message: discord.Message):
    error_message = None

    try:
        json.loads(message.content.replace("```json", "").replace("`", ""))
    except Exception as e:
        error_message = str(e)

    if not error_message:
        await interaction.response.send_message("JSON ok ✅")
        return
    
    await interaction.response.send_message(f"JSON not ok ❌\n\n{error_message}")

# Json validator hack as beebles asked for it
@bot.event
async def on_message(msg : discord.Message):
    if msg.author.bot:
        return

    if len(msg.attachments) <= 0:
        return

    filtered_attachments = [x for x in msg.attachments if "application/json" in x.content_type]

    if len(filtered_attachments) <= 0:
        return

    failed_results : list[str, str] = []

    for x in filtered_attachments:
        try:
            if x.size > 0x100000:
                raise Exception("File too big")

            json.loads(await x.read())
        except Exception as e:
            failed_results.append((x.filename, str(e)))

    if len(failed_results) <= 0:
        await msg.add_reaction("✅")
        return
    
    await msg.add_reaction("❌")
    await msg.reply(content="JSON Validation failed!\n\n" + "\n\n".join([f"**{x[0]}**\n{x[1]}" for x in failed_results]), mention_author=True)

@bot.event
async def on_ready():
    asyncio.create_task(mappings_manager_instance.update_mappings())
    logger.info(f'Logged in as {bot.user} (ID: {bot.user.id})')
    logger.info('------')

bot.run(bot_token)