# Extensions
Extensions are external programs or mods that you have added to your DCS installation like SRS, Tacview, etc. 
(supported ones, see below). DCSServerBot adds some support for them, reaching from simply displaying details about
them in your server embed (LotAtc) to completely starting and stopping external programs (SRS).

> ⚠️ **Attention!**<br>
> Besides MizEdit, which is my own solution, all other extensions are made by fellow community members. I am very happy 
> about these solutions and I really appreciate that someone put a lot of time in to make them what they
> are today.<br>
> Nevertheless, I am not responsible for them. Neither for any bugs, nor for their proper functionality. The developers
> usually either have their own Discord servers, where you can ask for support, or they have the option to raise an 
> issue in their GitHubs.<br>
> So please - if you see any issues in these solutions, contact the developers and ask for help.

## Supported Extensions
If you have looked around a bit, you might have seen already that I try to create APIs that you guys can use to extend
what is there. That said - there is a list of Extensions that I added already, but you can write our own. I'll give an
example later.

### MizEdit
This is not really an external solution supported by DCSServerBot, but my own one, which allows you to change your 
missions prior to the server startup.<br>
You can change more or less anything in the mission itself, like weather, mission parameters and even amend units, if
you like. The common use-case for people is to use it to change the weather on a timed or random basis.

As MizEdit is a very powerful solution, I decided to donate it a separate doc page, which you can reach [here](./MizEdit.md).

### OvGME
This little extension checks, if you have any requiredModules in your miz file and shows them in the server status
embed in Discord. Nice addition for your users, if you show them what to install to fly on your server.

The configuration is as simple as it sounds:
```yaml
MyNode:
  # [...]
  instances:
    DCS.release_server:
      # [...]
      extensions:
        OvGME:
          enabled: true
```

### DCS Voice Chat
If you want to use the built-in Voice Chat system of DCS, you can use the VoiceChat extension.
```yaml
MyNode:
  # [...]
  instances:
    DCS.release_server:
      # [...]
      extensions:
        VoiceChat:
          enabled: true
```

### SRS
[SimpleRadioStandalone](http://dcssimpleradio.com/) (DCS-SRS) is an awesome tool built by CiriBob, who dedicates a lot of work and this 
simulated real life radio experience to DCS. Many if not every server runs an SRS server too, to let their players have 
a proper radio experience.<br/>
DCSServerBot integrates nicely with SRS. If you place your server.cfg in your Saved Games\DCS(...)\Config folder (and I
usually rename it to SRS.cfg, just to avoid confusions in there), the bot can auto-start and -stop your SRS server 
alongside with your DCS server. It even monitors if SRS has crashed (that's a waste of code... I literally never saw
that crash) and start it again in such a case.<br/>
To enable SRS, support, you need to add the following parts to your nodes.yaml:
```yaml
MyNode:
  # [...]
  extensions:
    SRS:
      installation: '%ProgramFiles%\DCS-SimpleRadio-Standalone'
      autoupdate: true
  # [...]
  instances:
    DCS.release_server:
      # [...]
      extensions:
        SRS:
          config: '%USERPROFILE%\Saved Games\DCS.release_server\Config\SRS.cfg'
          host: 127.0.0.1
          port: 5002
          minimized: true     # start SRS minimized (default: true)
          autoconnect: true   # install the appropriate DCS-SRS-AutoConnectGameGUI.lua, default: true
          awacs: true
          blue_password: blue
          red_password: red
          autostart: true     # optional: if you manage your SRS servers outside of DCSSB, set that to false
          always_on: true     # start SRS as soon as possible  (includes no_shutdown: true)
          no_shutdown: true   # optional: don't shut down SRS on mission end (default: false)
          srs_message_prefix: 'SRS Running @ '        # optional: overwrite the message prefix
          srs_nudge_message: 'Optional nudge message' # optional: overwrite the existing nudge message
          
```
You need one entry in the node section, pointing to your DCS-SRS installation and one in every instance section, 
where you want to use SRS with. The next time the bot starts your server, it will auto-launch SRS and take care of it.

__Optional__ parameters (will change server.cfg if necessary):</br>
* **autoupdate** If true, SRS will check for updates and update itself.
* **host** The hostname or IP to be used in your DCS-SRS-AutoConnectGameGUI.lua. The bot will replace it in there.
* **port** SRS port (default: 5002)
* **awacs** AWACS mode
* **blue_password** AWACS mode, password blue.
* **red_password** AWACS mode, password red.
* **autostart** If true, the SRS server will be auto-started (default).

> ⚠️ **Attention!**<br>
> You need to disable User-Access-Control (UAC) to use SRS-autoupdate.

### Tacview
Many servers run [Tacview](https://www.tacview.net/) to help people analyse their flight path, weapons employment and 
whatnot. It is an awesome tool for teaching and after action reports as well.<br/>
One of the downsides (besides a performance hit on servers) is, that you gather a lot of data and fill up your disk.
DCSServerBot takes care of both, it will a) warn you, if you configured Tacview in a way that is bad for your overall
server performance, and b) it can delete old Tacview files after a specific time. (see below)<br/>

To enable Tacview support, a change in nodes.yaml is needed:
```yaml
MyNode:
  # [...]
  extensions:
    Tacview:
      tacviewExportPath: '%USERPROFILE%\Documents\Tacview'
  # [...]
  instances:
    DCS.release_server:
      # [...]
      extensions:
        Tacview:
          show_passwords: false  # hide passwords in your server status embed (default: true)
          host: 127.0.0.1        # Tacview host (default)
          log: "%USERPROFILE%\\Saved Games\\DCS.release_server\\Logs\tacview.log" # Only needed, if you export tacview logs to a different file.
          tacviewRealTimeTelemetryPort: 42674  # default
          tacviewRealTimeTelemetryPassword: '' # default
          tacviewRemoteControlPort: 42675      # default
          tacviewRemoteControlPassword: ''     # default
          tacviewPlaybackDelay: 600            # default 0, should be 600 for performance reasons
          target: '<id:112233445566778899>'    # optional: channel id or directory
```
__Optional__ parameters (will change options.lua if necessary):</br>
* **tacviewExportPath** Sets this as the Tacview export path.
* **tacviewRealTimeTelemetryPort** Sets this as the Tacview realtime port.
* **tacviewRealTimeTelemetryPassword** Sets this as the Tacview realtime password.
* **tacviewRemoteControlPort** Sets this as the Tacview remote control port.
* **tacviewRemoteControlPassword** Sets this as the Tacview remote control password.
* **tacviewPlaybackDelay** Sets this as the Tacview playback delay.
* **delete_after** specifies the number of days after which old Tacview files will get deleted by the bot.
* **show_passwords** specifies whether to show the Tacview passwords in the server embed in your status channel or not.
* **target** a channel or directory where your tacview files should be uploaded to on mission end.

To delete old tacview files, checkout the [Cleanup](../services/cleanup/README.md) service.

### LotAtc
Another famous extension for DCS is [LotAtc](https://www.lotatc.com/) by D'Art. If you think about any kind of proper
GCI or ATC work, there is no way around it. It perfectly integrates with DCS and DCS-SRS.<br/>
You'll get a notification in your servers status embed about ports and - if you like - passwords and the version of 
LotAtc printed in the footer. If a GCI gets active on your server, players of the respective coalition will be informed
via the in-game chat and a popup. Same if the GCI leaves their slot again.
```yaml
MyNode:
  # [...]
  instances:
    DCS.release_server:
      # [...]
      extensions:
        LotAtc:
          show_passwords: false     # show passwords in the server status embed (default = true)
          host: "myfancyhost.com"   # Show a different hostname instead of your servers external IP
          port: 10310               # you can specify any parameter from LotAtc's config.lua in here to overwrite it
```
There is no default section for LotAtc, so if added to an instance like described above, it is enabled, if not, then not.

### DSMC
If you want to enable persistence for your missions, [DSMC](https://dsmcfordcs.wordpress.com/) is one way to go.
DSMC does not need any change in your missions (but you can, see their documentation!). It will write out a new
miz-file with the state of the mission at the time of saving. This is perfect for instance for campaigns, where you
want to follow up on the next campaign day with the exact state of the mission it had at the end of the current day.</br>
To use DSMC, you need to install it, according to the documentation linked above. In DCSServerBot, you activate the 
extension like with all others:
```yaml
MyNode:
  # [...]
  instances:
    DCS.release_server:
      # [...]
      extensions:
        DSMC:
          enabled: true
```
DCSServerBot will detect if DSMC is enabled and - if yes - change the settings in your DSMC_Dedicated_Server_options.lua
to fit to its needs. DSMC will write out a new miz-file with a new extension (001, 002, ...) after each run. The bot
will take care, that this generated mission will be the next to launch. Other extensions like RealWeather work together
with these generated missions, so you can use a DSMC generated mission but apply a preset or any real time weather to
it.

### Sneaker
Well, this "sneaked" in here somehow. Many people were asking for a moving map, and we looked at several solutions. 
Nearly all took a lot of effort to get them running, if ever. Then we stumbled across 
[Sneaker](https://github.com/b1naryth1ef/sneaker) and in all fairness - that was more or less all that we needed. It 
looks good, it is easy to set up. We tried to contact the developer, but unfortunately they are quite unresponsive. So
we created a [fork](https://github.com/Special-K-s-Flightsim-Bots/sneaker), added all the maps and maybe will remove
the major bugs in the upcoming future.<br/>
Sneaker itself provides a webserver that then connect via the Tacview Realtime protocol to your server. You need to 
have Tacview running on your server though, to use sneaker. As there are still some issues, please don't configure a
realtime password for now.<br/>
Adding sneaker is quite straightforward, if you looked at the above examples already:
```yaml
MyNode:
  # [...]
  extensions:
    Sneaker:
      cmd: '%USERPROFILE%\Documents\GitHub\sneaker\sneaker.exe'
      bind: 0.0.0.0:8080            # local listen configuration for Sneaker
      url: https://myfancyhost.com  # optional: show a different host instead of the servers external IP
  # [...]
  instances:
    DCS.release_server:
      # [...]
      extensions:
        Sneaker:
          enabled: true
          debug: true               # Show the Sneaker console output in the DCSSB console. Default = false
```
You need to let the sneaker cmd point to wherever you've installed the sneaker.exe binary (name might vary, usually 
there is a version number attached to it). DCSServerBot will auto-create the config.json for sneaker 
(config/sneaker.json) and start / stop / monitor the sneaker process.

### DCS Real Weather Updater
If you want to use real-time weather in your missions, you can do that by using [DCS-real-weather](https://github.com/evogelsa/DCS-real-weather).
Download the release zip and unzip it to a directory of your choice on your system running your DCS servers and the 
DCSServerBot. You can then add another extension into your nodes.yaml:
```yaml
MyNode:
  # [...]
  extensions:
    RealWeather:
      installation: '%USERPROFILE%\Documents\realweather_v1.9.0-rc2'
  # [...]
  instances:
    DCS.release_server:
      # [...]
      extensions:
        RealWeather:
          enabled: true   # optional to disable the extension, default: true
          debug: true     # see outputs of RealWeather, default: false
          metar:
            icao: URMM
            runway-elevation: 50
            add-to-brief: true
          options:
            update-time: true
            update-weather: true
            wind:
              minimum: 0
              maximum: 5
              gust-minimum: 0
              gust-maximum: 10
              stability: 0.143
            clouds:
              disallowed-presets:
                - Preset10
                - RainyPreset1
                - RainyPreset2
                - RainyPreset3
            fog:
              enabled: true
              thickness-minimum: 0
              thickness-maximum: 100
              visibility-minimum: 1000
              visibility-maximum: 4000
            dust:
              enabled: true
              visibility-minimum: 300
              visibility-maximum: 2000
```
You can find a list of supported parameters in the config.json provided by DCS-real-weather.<br>
> ⚠️ **Attention!**<br>
> DCSServerBot only supports DCS Real Weather Updater versions from 1.9.0 upwards.
> 
> If you want to set a custom ICAO code (URMM in this case) per mission, you can name your mission like so:<br>
> `MyFancyMission_ICAO_URMM_whatsoever.miz`

### Lardoon
[Lardoon](https://github.com/b1naryth1ef/lardoon) is another web-server-based application that provides a nice search 
interface for Tacview files. It is based on [Jambon](https://github.com/b1naryth1ef/jambon) an ACMI parser.</br>
You can use it more or less like Sneaker. It contains of a single server instance, that runs on a specific port, and
it'll import all Tacview files from all your servers. You can access the gui with your browser.

```yaml
MyNode:
  # [...]
  extensions:
    Lardoon:
      cmd: '%USERPROFILE%\Documents\GitHub\lardoon\lardoon.exe'
      minutes: 5                    # Number of minutes the Lardoon database is updated
      bind: 0.0.0.0:3113            # IP and port the Lardoon server is listening to
      url: https://myfancyhost.com  # Alternate hostname to be displayed in your status embed 
  # [...]
  instances:
    DCS.release_server:
      # [...]
      extensions:
        Lardoon:
          enabled: true
          debug: true               # Show the Lardoon console output in the DCSSB console. Default = false
          tacviewExportPath: 'G:\My Drive\Tacview Files'  # Alternative drive for tacview files (default: auto-detect from Tacview)
```
Don't forget to add some kind of security before exposing services like that to the outside world, with for instance
a nginx reverse proxy.</br>
If you plan to build Lardoon on your own, I'd recommend the fork of [Team LimaKilo](https://github.com/team-limakilo/lardoon).

### DCS Olympus (v1.0.4 and above, for v1.0.3 see below)
[DCS Olympus](https://github.com/Pax1601/DCSOlympus) is a free and open-source mod for DCS that enables dynamic 
real-time control through a map interface. It is a mod that needs to be installed into your servers. Best you can do
is to download the latest ZIP file from [here](https://github.com/Pax1601/DCSOlympus/releases/latest) and provide it to the [OvGME](../services/ovgme/README.md) service like so:
```yaml
DEFAULT:
  SavedGames: '%USERPROFILE%\Documents\OvGME\SavedGames'
  RootFolder: '%USERPROFILE%\Documents\OvGME\RootFolder'
DCS_MERCS:
  packages:
  - name: DCSOlympus
    version: latest
    source: SavedGames
```
To use the DCS Olympus client, you need [Node.js](https://nodejs.org/dist/v20.10.0/node-v20.10.0-x64.msi) installed.
Click on the link, download and install it. Remember the installation location, as you need to provide it in the 
configuration.

Then you can add the DCS Olympus extension like so to your nodes.yaml:
```yaml
MyNode:
  # [...]
  extensions:
    Olympus:
      nodejs: '%ProgramFiles%\nodejs'
  # [...]
  instances:
    DCS.release_server:
      # [...]
      extensions:
        Olympus:
          debug: true                     # Show the Olympus console in the DCSSB console, default = false
          show_passwords: true            # show passwords in your server status embed (default: false)
          url: http://myfancyurl:3000/    # optional: your own URL, if available
          backend:
            port: 3001                    # server port for DCS Olympus internal communication (needs to be unique)                   
          authentication:
            gameMasterPassword: secret    # Game Master password
            blueCommanderPassword: blue   # Blue Tactical Commander password
            redCommanderPassword: red     # Red Tactical Commander password
          frontend:
            path: '%USERPROFILE%\Saved Games\Olympus\frontend' # Optional: path to the Olympus frontend. This is only needed if you are using the official installer. OVGME users don't need this.
            port: 3000                    # Port where DCS Olympus listens for client access (needs to be unique)
    instance2:
      # [...]
      extensions:
        Olympus:
          enabled: false                  # Don't enable DCS Olympus on your instance2
```
> ⚠️ **Attention!**<br>
> You need to forward the frontend port from your router to the PC running DCS and DCS Olympus.

### DCS Olympus (v1.0.3)
[DCS Olympus](https://github.com/Pax1601/DCSOlympus) is a free and open-source mod for DCS that enables dynamic 
real-time control through a map interface. It is a mod that needs to be installed into your servers. Best you can do
is to download the latest ZIP file from [here](https://github.com/Pax1601/DCSOlympus/releases/latest) and provide it to the [OvGME](../services/ovgme/README.md) service like so:
```yaml
DEFAULT:
  SavedGames: '%USERPROFILE%\Documents\OvGME\SavedGames'
  RootFolder: '%USERPROFILE%\Documents\OvGME\RootFolder'
DCS_MERCS:
  packages:
  - name: DCSOlympus
    version: latest
    source: SavedGames
```
To use the DCS Olympus client, you need [Node.js](https://nodejs.org/dist/v20.10.0/node-v20.10.0-x64.msi) installed.
Click on the link, download and install it. Remember the installation location, as you need to provide it in the 
configuration.

Then you can add the DCS Olympus extension like so to your nodes.yaml:
```yaml
MyNode:
  # [...]
  extensions:
    Olympus:
      nodejs: '%ProgramFiles%\nodejs'
  # [...]
  instances:
    DCS.release_server:
      # [...]
      extensions:
        Olympus:
          debug: true                     # Show the Olympus console in the DCSSB console, default = false
          show_passwords: true            # show passwords in your server status embed (default: false)
          url: http://myfancyurl:3000/    # optional: your own URL, if available
          server:
            address: '*'                  # your bind address. * = 0.0.0.0, use localhost for local only setups
            port: 3001                    # server port for DCS Olympus internal communication (needs to be unique)                   
          authentication:
            gameMasterPassword: secret    # Game Master password
            blueCommanderPassword: blue   # Blue Tactical Commander password
            redCommanderPassword: red     # Red Tactical Commander password
          client:
            port: 3000                    # Port where DCS Olympus listens for client access (needs to be unique)
    instance2:
      # [...]
      extensions:
        Olympus:
          enabled: false                  # Don't enable DCS Olympus on your instance2
```
> ⚠️ **Attention!**<br>
> You need to forward the server.port and the client.port from your router to the PC running DCS and DCS Olympus.<br>
> To create an exclusion in your UAC run this: `netsh http add urlacl url="http://*:3001/olympus/" user=user-running-dcs`

### DCS-gRPC
[DCS-gRPC](https://github.com/DCS-gRPC) is a communication library, that is somehow similar to what DCSServerBot does 
already. It has some differences though and comes with some other tools. This said, you can use it alongside DCSServerBot
without issues.<br>
The extension itself allows you to configure your DCS-gRPC server from your instance configurations like with any other
extension:
```yaml
MyNode:
  # [...]
  instances:
    DCS.release_server:
      # [...]
      extensions:
        gRPC:
          enabled: true
          port: 50051     # you can set any configuration parameter here, that will be replaced in your dcs-grpc.lua file.
```

### Pretense
[Pretense](https://github.com/Dzsek/pretense) is a dynamic campaign system built by Dzsek. Unfortunately he dropped
the development of it lately, but it is still in use by many and great missions to run on your servers. That is why
I decided to not drop the support for now in DCSServerBot.<br>
The main part happens in the [Pretense](../plugins/pretense/README.md) plugin, where you can run commands and configure
specific statistic displays. But you can also use this small extension to either display your users which version of
Pretense you are using and to have a very basic configuration of it. Just add some lines to your nodes.yaml like so:
```yaml
MyNode:
  # [...]
  instances:
    DCS.release_server:
      # [...]
      extensions:
        Pretense:
          randomize: true # puts a randomize.lua in your Missions\Saves directory. See the Pretense documentation for more.
```

### Write your own Extension!
Do you use something alongside with DCS that isn't supported yet? Are you someone that does not fear some lines of
Python code? Well then - write your own extension!</br>
<p>
Just implement a python class, extend core.Extension and configure it in your nodes.yaml:

```python
from core import Extension, report
from discord.ext import tasks
from typing import Optional


class MyExtension(Extension):

    async def prepare(self) -> bool:
        await super().prepare()
        # do something that has to happen, before the DCS server starts up
        return True

    async def startup(self) -> bool:
        await super().startup()
        self.log.debug("Hello World!")
        return True

    def shutdown(self) -> bool:
        self.log.debug("Cya World!")
        return super().shutdown()

    def is_running(self) -> bool:
        return True

    @property
    def version(self) -> str:
        return "1.0.0"

    def render(self, embed: report.EmbedElement, param: Optional[dict] = None):
        embed.add_field(name='MyExtension', value='enabled' if self.is_running() else 'disabled')

    @tasks.loop(hours=24.0)
    async def schedule(self):
        # if you need to run something on a scheduled basis, you can do that in here (optional)
        pass
```

You can then use this extension in your nodes.yaml like so:
```yaml
MyNode:
  # [...]
  extensions:
    mymodule.MyExtension:
      param1: aa
      param2: bb
  # [...]
  instances:
    DCS.release_server:
      # [...]
      extensions:
        mymodule.MyExtension:
          enabled: true
          param3: cc
          param4: dd
```
