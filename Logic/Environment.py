
from copy         import deepcopy
from threading    import Thread
from Logic.Global import printf, FpsTimer
from Logic.Vision import Vision
from Logic        import Video, Events, Commands, ObjectManager, Robot, Global

"""
    Rules of thumb:
        - Objects that do not change while the interpreter is running are stored in Environment
            - Vision Objects
            - Movement Paths
        - Objects that are added and modified while interpeter is running are stored in the Interpeter
            - Variables generated by commands
            - "on the fly" vision objects generated by commands
"""

class Environment:
    """
    Environment is a singleton. Do not create more than one! It is intended to help cope with the needs of GUI
    programming, where at any point I might need access to the robot or to video. However, some rules apply: if
    Environment is passed to a class, do not save it to the class, instead pull what is needed from there and save
    that to the class instead.


    Environment holds the following thing and handles their shutdown:
        - VideoStream object
        - Vision object
        - Robot object
        - ObjectManager object


    THE ENVIRONMENT DOES NOT HOLD THE INTERPRETER, BY DESIGN: Since an Interpreter can run an interpreter inside of it,
    recursively, then the environment must not hold an interpreter. They are seperate.
    """

    def __init__(self, settings):
        # Initialize Global Variables
        Global.init()


        # Set up environment objects
        self.__vStream    = Video.VideoStream()           # Gets frames constantly
        self.__robot      = Robot.Robot()
        self.__vision     = Vision(self.__vStream)  # Performs computer vision tasks, using images from vStream
        self.__settings   = settings
        self.__objectMngr = ObjectManager.ObjectManager()


        # If the settings have any information, try to instantiate objects. Otherwise, GUI will do this as user requests
        if settings['cameraID'] is not None:
            self.__vStream.setNewCamera(settings['cameraID'])

        if settings['robotID'] is not None:
            self.__robot.setUArm(settings['robotID'])

        self.__objectMngr.loadAllObjects()



        # This keeps track of objects that have been self.addObject()'d, so self.saveObjects() actually saves them.
        # self.changedObjects = []


    # Getting System Objects
    def getRobot(self):
        return self.__robot

    def getVStream(self):
        return self.__vStream

    def getVision(self):
        return self.__vision

    def getSettings(self):
        return deepcopy(self.__settings)

    def getObjectManager(self):
        return self.__objectMngr




    # Close system objects
    def close(self):
        # This will try to safely shut down any objects that are capable of running threads.
        self.__robot.setExiting(True)
        self.__vision.setExiting(True)
        self.__vStream.endThread()


class Interpreter:
    def __init__(self):
        self.mainThread   = None    # The thread on which the script runs on. Is None while thread is not running.
        self.__exiting    = True    # When True, the script thread will attempt to close ASAP
        self.scriptFPS    = 50      # Speed at which events are checked
        self.events       = []      # A list of events, and their corresponding commands

        # For self.getStatus()
        self.currRunning  = []      # A dictionary of what has been run so far in the loop {eventIndex:[commandIndex's]}

        # Should only be interacted with through self.setVariable()
        self.__variables  = {}


    # Functions for GUI to use
    def loadScript(self, script, env):
        """
        Creates each event, loads it with its appropriate commandList, and then adds that event to self.events

        :param      env: Environment object
        :param      script: a loaded script from a .task file
        :return:    any errors that commands returned during instantiation
        """
        print("script", script)
        script = deepcopy(script)

        errors = {}  # Errors are returned from

        # Create each event
        for _, eventSave in enumerate(script):
            # Get the "Logic" code for this event, stored in Events.py
            eventType = getattr(Events, eventSave['typeLogic'])
            event     = eventType(env, self, parameters=eventSave['parameters'])
            self.addEvent(event)

            # Add any commands related to the creation of this event
            for error in event.errors:
                    if error not in errors: errors[error] = []
                    errors[error].append(eventSave['typeLogic'])


            # Create the commandList for this event
            for _, commandSave in enumerate(eventSave['commandList']):
                # Get the "Logic" code command, stored in Commands.py
                commandType = getattr(Commands, commandSave['typeLogic'])
                command     = commandType(env, self, commandSave['parameters'])
                event.addCommand(command)

                for error in command.errors:
                    if error not in errors: errors[error] = []
                    errors[error].append(commandSave['typeLogic'])


        # Get rid of repeat errors
        # errors = set(errors)
        printf("Interpreter.loadScript(): The following errors occured during loading: ", errors)
        return errors



    # Generic Functions for API and GUI to use
    def startThread(self, robot, vision):
        # Start the program thread
        if self.mainThread is None:
            # Make sure vision and robot are not in exiting mode
            vision.setExiting(False)
            robot.setExiting(False)
            robot.setActiveServos(all=True)
            robot.setSpeed(10)

            self.__exiting     = False
            self.mainThread  = Thread(target=self.__programThread)
            self.currRunning = {}
            self.mainThread.start()
        else:
            printf("Interpreter.startThread(): ERROR: Tried to run programThread, but there was already one running!")

    def endThread(self, robot, vision):
        # Close the thread that is currently running at the first chance it gets. Return True or False
        printf("Interpreter.endThread(): Closing program thread.")

        self.__exiting = True

        if self.mainThread is not None:
            # DeActivate Vision and the Robot so that exiting the thread will be very fast
            vision.setExiting(True)
            robot.setExiting(True)

            self.mainThread.join(3000)

            if self.mainThread.is_alive():
                printf("Interpreter.endThread(): ERROR: Thread was told to close but did not")
                return False

            # Re-Activate the Robot and Vision, for any future uses
            vision.setExiting(False)
            robot.setExiting(False)
            vision.endAllTrackers()

            # Clean up interpreter variables
            self.mainThread = None
            self.events     = []


            # Reset self.__variables with the default values/functions #
            safeList = ['math', 'acos', 'asin', 'atan', 'atan2', 'ceil', 'cos', 'cosh', 'degrees',
                        'e', 'exp', 'fabs', 'floor', 'fmod', 'frexp', 'hypot', 'ldexp', 'log', 'log10',
                        'modf', 'pi', 'pow', 'radians', 'sin', 'sinh', 'sqrt', 'tan', 'tanh']


            # Use the list to filter the local namespace
            self.__variables        = {}
            self.__variables        = dict([(k, locals().get(k, None)) for k in safeList])


            # Add any needed builtins back in.
            self.__variables['abs']    = abs
            self.__variables['robot']  = robot
            self.__variables['vision'] = vision

            return True

    def addEvent(self, event):
        self.events.append(event)


    def isRunning(self):
        return not self.__exiting or self.mainThread is not None

    def isExiting(self):
        # Commands that have the potential to take a long time (wait, pickup, that sort of thing) will use this to check
        # if they should exit immediately
        return self.__exiting

    def getStatus(self):
        # Returns an index of the (event, command) that is currently being run

        if self.isExiting():
            return False

        currRunning = self.currRunning

        return currRunning


    # The following functions should never be called by user - only for Commands/Events to interact with Interpreter
    def setVariable(self, name, expression):
        """
        Sets a variable to an expression in the interpreter. It will first check if the variable exists, if not, add it
        to the self.__variables dict with a default initial value of 0, evaluate the expression, then set the variable
        to the new value. If the expression did not evaluate (If an error occured) it will simply not set the variable
        to the new value.

        :param name: String, variable name
        :param expression: String, expression that evaluates to a number
        :return:
        """

        # If the variable has not been set before, add it to the variables namespace, and assign it a value of 0
        if name not in self.__variables:
            self.__variables[name] = 0

        newValue, success = self.evaluateExpression(expression)

        self.__variables[name] = newValue
        # if success:
        #     self.__variables[name] = round(newValue, 8)

    def getVariable(self, name):
        # Gets the value of a variable. Returns "Value, Success". Success will be false if the variable did not exist
        # Value is 0 by default
        if name not in self.__variables:
            return 0, False
        else:
            return self.__variables[name], True

    def evaluateExpression(self, expression):
        # Returns value, success. Value is the output of the expression, and 'success' is whether or not it crashed.
        # If it crashes, it returns None, but some expressions might return none, so 'success' variable is still needed.
        # Side note: I would have to do ~66,000 eval operations to lag the program by one second.

        answer = None
        try:
            answer = eval(expression, {"__builtins__": None}, self.__variables)
        except:
            printf('Interpreter.__evaluateExpression(): ERROR: Expression "', expression, '" crashed!')
            return None, False

        if answer is None:

            return None, False

        return answer, True

    def evaluateScript(self, script, env):
        # Returns value, success. Value is the output of the expression, and 'success' is whether or not it crashed.
        # If it crashes, it returns None, but some expressions might return none, so 'success' variable is still needed.
        # Side note: I would have to do ~66,000 eval operations to lag the program by one second.

        answer = None

        robot = env.getRobot()
        vision = env.getVision()
        try:
            exec(script)
        except:
            printf('Interpreter.__evaluateExpression(): ERROR: Script "', script, '" crashed!')
            return None, False

        if answer is None:

            return None, False

        return answer, True


    # The following functions are *only* for interpreter to use within itself.
    def __programThread(self):
        # This is where the script you create actually gets run.
        print("\n\n\n ##### STARTING PROGRAM #####\n")

        # self.env.getRobot().setServos(servo1=True, servo2=True, servo3=True, servo4=True)
        # self.env.getRobot().refresh()

        timer = FpsTimer(fps=self.scriptFPS)

        while not self.__exiting:
            timer.wait()
            if not timer.ready(): continue


            # Check every event, in order of the list
            self.currRunning = {}

            for index, event in enumerate(self.events):
                if self.isExiting(): break
                if not event.isActive(): continue
                self.__interpretEvent(event)


        # Check if a DestroyEvent exists, if so, run it's commandList
        destroyEvent = list(filter(lambda event: type(event) == Events.DestroyEvent, self.events))

        if len(destroyEvent): self.__interpretEvent(destroyEvent[0], overrideKillApp=True)

    def __interpretEvent(self, event, overrideKillApp=False,):
        # This will run through every command in an events commandList, and account for Conditionals and code blocks.
        eventIndex    = self.events.index(event)
        commandList   = event.commandList
        index         = 0                   # The current command that is being considered for running
        # currentIndent = 0                   # The 'bracket' level of code

        self.currRunning[eventIndex] = []

        # Check each command, run the ones that should be run
        while index < len(event.commandList):
            if self.isExiting() and not overrideKillApp: break  # THis might be overrun for things like DestroyEvent

            command    = commandList[index]

            # Run command. If command is a boolean, it will return a True or False
            self.currRunning[eventIndex].append(index)
            evaluation = command.run()


            # If the command returned an "Exit event" command, then exit the event evaluation
            if evaluation == "Exit": break


            # If its false, skip to the next indent of the same indentation, or an "Else" command
            if evaluation is False:
                index = self.__getNextIndex(index, commandList)
            else:
                # If an else command is the next block, decide whether or not to skip it
                if index + 1 < len(commandList) and type(commandList[index + 1]) is Commands.ElseCommand:
                    # If the evaluation was true, then DON'T run the else command

                    index = self.__getNextIndex(index + 1, commandList)


            index += 1

    def __getNextIndex(self, index, commandList):
        # If its false, skip to the next indent of the same indentation, or an "Else" command


        skipToIndent = 0
        nextIndent   = 0
        for i in range(index + 1, len(commandList)):
            if type(commandList[i]) is Commands.StartBlockCommand: nextIndent += 1

            if nextIndent == skipToIndent:
                index = i - 1
                break

            # If there are no commands
            if i == len(commandList) - 1:
                index = i
                break


            if type(commandList[i]) is Commands.EndBlockCommand:   nextIndent -= 1



        return index
