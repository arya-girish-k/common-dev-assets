import argparse
import json
import logging
import os
from typing import Any, Dict, List

import requests
from ibm_cloud_sdk_core.authenticators import IAMAuthenticator
from ibm_platform_services.catalog_management_v1 import CatalogManagementV1


def get_tokens(api_key: str) -> (str, str):
    try:
        iam_url = "https://iam.cloud.ibm.com/identity/token"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        data = {
            "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
            "apikey": api_key,
        }

        response = requests.post(iam_url, headers=headers, data=data)
        logging.debug(response)
        response_json = response.json()

        return response_json.get("access_token"), response_json.get("refresh_token")
    except Exception as e:
        logging.error(f"Error getting tokens: {str(e)}")
        return None, None


def get_version(locator_id: str, api_key: str):
    authenticator = IAMAuthenticator(api_key)
    service = CatalogManagementV1(authenticator=authenticator)
    try:
        response = service.get_version(version_loc_id=locator_id).get_result()
        logging.debug(response)
        return response
    except Exception as e:
        logging.error(f"Error getting version {locator_id}: {str(e)}")
        return None


def get_version_updates(offeringId, catalogId, kind, flavor, api_key):
    authenticator = IAMAuthenticator(api_key)
    service = CatalogManagementV1(authenticator=authenticator)
    _, refresh_token = get_tokens(api_key)
    try:
        response = service.get_offering_updates(
            catalog_identifier=catalogId,
            offering_id=offeringId,
            kind=kind,
            x_auth_refresh_token=refresh_token,
        ).get_result()
        logging.debug(response)
        # filter updates by flavor name
        response = [
            update
            for update in response
            if "flavor" in update.keys() and update["flavor"]["name"] == flavor
        ]
        logging.debug(f"filtered response: {response}")
        return response
    except KeyError as e:
        logging.error(
            f"KeyError: {str(e)} in update dictionary. Please ensure the update dictionary has the correct "
            f"structure."
        )
        return None
    except Exception as e:
        logging.error(f"Error getting version updates for {offeringId}: {str(e)}")
        return None


def get_latest_valid_version(updates: List[Dict[str, Any]]):
    try:
        # sort updates by state.current_entered
        updates = sorted(
            updates, key=lambda x: x["state"]["current_entered"], reverse=True
        )
        logging.debug(f"sorted updates: {updates}")
        # get the latest version that is not deprecated and consumable
        for update in updates:
            logging.debug(f"Checking update: {update}")
            if update["can_update"] and update["state"]["current"] == "consumable":
                return update
        return None
    except Exception as e:
        logging.error(f"Error getting latest valid version: {str(e)}")
        return None


if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="Update Stack Memeber Versions")
    parser.add_argument(
        "--stack",
        "-s",
        type=str,
        action="store",
        dest="stack",
        help="path stack definition json",
        required=True,
    )
    parser.add_argument(
        "--api-key",
        "-k",
        type=str,
        action="store",
        dest="api_key",
        help="IBM Cloud API Key, if not set, use IBM_CLOUD_API_KEY environment variable",
        required=False,
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        dest="debug",
        help="Enable debug logging",
        default=False,
    )
    parser.add_argument(
        "--dry-run",
        "-d",
        action="store_true",
        dest="dry_run",
        help="Dry run mode, do not update stack definition",
        required=False,
    )

    args = parser.parse_args()

    # if api key passed as argument, use it or else use the environment variable, error if not set
    if args.api_key:
        api_key = args.api_key
    else:
        api_key = os.environ.get("IBM_CLOUD_API_KEY")

    if not api_key:
        logging.error(
            "IBM_CLOUD_API_KEY environment variable not set or passed as argument"
        )
        # print argument help
        parser.print_help()
        exit(1)

    # switch log level to DEBUG if passed as argument
    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    # check if stack definition json file exists
    if not os.path.exists(args.stack):
        logging.error(f"Stack definition file {args.stack} not found")
        exit(1)

    catalogs = {}  # Cache catalogs to avoid multiple requests
    failures = []  # List to track failures

    # read stack definition json
    with open(args.stack, "r") as f:
        stack_json = f.read()
        stack = json.loads(stack_json)
        logging.debug(f"Stack definition: {stack}")
        updates_made = False
        #     loop through each stack member
        for member in stack["members"]:
            try:
                logging.info(f"Updating {member['name']}")
                # split locator on . first part is the catalog id second is the version id
                version_locator = member["version_locator"]
                catalogId, versionId = version_locator.split(".")
                logging.debug(version_locator)
                version = get_version(version_locator, api_key)
                if version is None:
                    logging.error(
                        f"Failed to get version for {member['name']}: {version_locator}"
                    )
                    failures.append(
                        f"Failed to get version for {member['name']}: {version_locator}"
                    )
                    continue
                logging.debug(
                    f"current version: {version.get('kinds', [])[0].get('versions')[0].get('version')}"
                )
                kind = version.get("kinds", [])[0].get("format_kind")
                flavor = (
                    version.get("kinds", [])[0]
                    .get("versions")[0]
                    .get("flavor")
                    .get("name")
                )
                offeringId = version.get("id", {})
                updates = get_version_updates(
                    offeringId, catalogId, kind, flavor, api_key
                )
                if updates is None:
                    logging.error(f"Failed to get version updates for {offeringId}\n")
                    failures.append(f"Failed to get version updates for {offeringId}")
                    continue
                latest_version = get_latest_valid_version(updates)
                if latest_version is None:
                    logging.error(f"Failed to get latest valid version for {updates}\n")
                    failures.append(f"Failed to get latest valid version for {updates}")
                    continue
                latest_version_locator = latest_version.get("version_locator")
                latest_version_name = latest_version.get("version")
                current_version = (
                    version.get("kinds", [])[0].get("versions")[0].get("version")
                )
                logging.info(f"current version: {current_version}")
                logging.info(f"latest version: {latest_version_name}")
                logging.info(f"latest version locator: {latest_version_locator}")
                if current_version != latest_version_name:
                    logging.info(
                        f"Updating {member['name']} to version {latest_version_name}\n"
                    )
                else:
                    logging.info(
                        f"{member['name']} is already up to date. No updates were made.\n"
                    )
                # check if the version locator has changed
                if member["version_locator"] != latest_version_locator:
                    # update stack member with latest version locator
                    member["version_locator"] = latest_version_locator
                    # set flag to True
                    updates_made = True

            except Exception as e:
                logging.error(f"Error updating member {member['name']}: {str(e)}\n")
                failures.append(f"Error updating member {member['name']}: {str(e)}")

    # write updated stack definition to file only if updates were made
    if updates_made:
        if args.dry_run:
            logging.info("Dry run mode, no updates were made to stack definition")
        else:
            with open(args.stack, "w") as f:
                f.write(json.dumps(stack, indent=2) + "\n")
            logging.info(f"Stack definition updated: {args.stack}")
    else:
        logging.info("Already up to date. No updates were made.")

    # Print summary of failures and exit with error code if any failures occurred
    if failures:
        failureString = "\n".join(failures)
        logging.error(f"\nSummary of failures:\n{failureString}")
        exit(1)
