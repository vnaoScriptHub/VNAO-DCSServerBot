# Cleanup Service
The cleanup service is your cleaning lady, that deletes all the unnecessary stuff from your disk or discord channels 
after some time.

## Configuration
The configuration is held in config/services/cleanup.yaml and is straight forward. You can add as many directories
or channels as you want to clean up in here.

```yaml
DEFAULT:
  dcs.log:                                # Name (can be anything but needs to be unique)
    directory: "{instance.home}/Logs"     # The directory to clean up
    pattern: "*.*"                        # The pattern of the files to be cleaned up
    delete_after: 30                      # The min age of the files to be deleted (default: 30)
  trackfiles:
    directory: "{instance.home}/Tracks/Multiplayer"
    pattern: "*.trk"
    delete_after: 30
DCS.release_server:
  greenieboard:
    directory: "{instance.home}/airboss"
    pattern:
    - "*.csv"
    - "*.png"
    delete_after: 30
  tacview:
    directory: "%USERPROFILE%/Documents/Tacview"
    pattern: "*.acmi"
    recursive: true                       # If true, subdirectories will be included
    delete_after: 30
  channels:
    channel:                # delete all messages from these channels ...
      - 112233445566778899
      - 998877665544332211
    delete_after: 7         # ... which are older than 7 days (default: 0)
```
These are just examples, feel free to add your own directories / channels.
> ⚠️ **Attention!**<br>
> Please keep in mind that deleting a lot of messages will take its time and can result in Discord rate limits.
