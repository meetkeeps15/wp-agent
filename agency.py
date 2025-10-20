from dotenv import load_dotenv
from agency_swarm import Agency

from wizard_designer import wizard_designer

import asyncio

load_dotenv()

# do not remove this method, it is used in the main.py file to deploy the agency (it has to be a method)
def create_agency(load_threads_callback=None):
    agency = Agency(
        wizard_designer,
        communication_flows=[],
        name="WizardAgency", # don't forget to rename your agency!
        shared_instructions="shared_instructions.md",
        load_threads_callback=load_threads_callback,
        max_output_tokens=18000,
    )

    return agency

if __name__ == "__main__":
    agency = create_agency()

    # test 1 message
    # async def main():
    #     response = await agency.get_response("Hello, how are you?")
    #     print(response)
    # asyncio.run(main())

    # run in terminal
    agency.terminal_demo()